from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import DB, not_found
from app.enums import AssetClass, TxType
from app.models import Asset, Portfolio, Transaction
from app.schemas import (
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
)
from app.services.market_data import (
    get_or_create_asset,
    quote_for_date,
    sync_payment_schedule,
    sync_price_history,
)
from app.services.moex import MoexISS
from app.services.portfolio import EPS, available_quantity, load_transactions

router = APIRouter(prefix="/transactions", tags=["Сделки"])

NO_ASSET_TYPES = {TxType.DEPOSIT, TxType.WITHDRAWAL, TxType.TAX, TxType.FEE}


def _serialize(tx: Transaction) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        portfolio_id=tx.portfolio_id,
        portfolio_name=tx.portfolio.name if tx.portfolio else None,
        asset_id=tx.asset_id,
        secid=tx.asset.secid if tx.asset else None,
        asset_name=(tx.asset.short_name or tx.asset.name) if tx.asset else None,
        asset_class=tx.asset.asset_class if tx.asset else None,
        type=tx.type,
        executed_at=tx.executed_at,
        price=float(tx.price),
        quantity=float(tx.quantity),
        amount=float(tx.amount),
        fee=float(tx.fee),
        tax=float(tx.tax),
        accrued_interest=float(tx.accrued_interest),
        currency=tx.currency,
        note=tx.note,
        source=tx.source,
        do_not_adjust_cash=tx.do_not_adjust_cash,
    )


async def _available_to_sell(
    db: AsyncSession,
    portfolio_id: int,
    asset_id: int,
    executed_at: datetime,
    exclude_id: int | None = None,
) -> float:
    """Остаток бумаги перед сделкой с учётом порядка сделок внутри дня."""
    history = await load_transactions(db, [portfolio_id])
    # у новой сделки ещё нет id — она встанет после всех сделок того же дня
    before = (executed_at, float("inf") if exclude_id is None else exclude_id)
    return available_quantity(history, asset_id, before, exclude_id=exclude_id)


async def _ensure_sellable(
    db: AsyncSession,
    portfolio_id: int,
    asset: Asset,
    quantity: float,
    executed_at: datetime,
    exclude_id: int | None = None,
) -> None:
    """Проверяет, что продажа не превышает остаток в портфеле."""
    if quantity <= EPS:
        raise HTTPException(400, "Количество в продаже должно быть больше нуля")

    available = await _available_to_sell(
        db, portfolio_id, asset.id, executed_at, exclude_id=exclude_id
    )
    if available <= EPS:
        raise HTTPException(
            400,
            f"В портфеле нет бумаг {asset.secid} на {executed_at:%d.%m.%Y}. "
            "Продать можно только то, что уже куплено",
        )
    if quantity > available + EPS:
        raise HTTPException(
            400,
            f"Нельзя продать {quantity:g} {asset.secid}: "
            f"в портфеле только {available:g}",
        )


@router.get("", response_model=TransactionPage, summary="Список сделок")
async def list_transactions(
    db: DB,
    portfolio_id: list[int] | None = Query(None),
    secid: str | None = None,
    type: list[TxType] | None = Query(None),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    search: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
):
    stmt = select(Transaction).options(
        selectinload(Transaction.asset), selectinload(Transaction.portfolio)
    )
    count_stmt = select(func.count(Transaction.id))

    conditions = []
    if portfolio_id:
        conditions.append(Transaction.portfolio_id.in_(portfolio_id))
    if type:
        conditions.append(Transaction.type.in_([t.value for t in type]))
    if date_from:
        conditions.append(Transaction.executed_at >= date_from)
    if date_to:
        conditions.append(Transaction.executed_at <= date_to)
    if secid:
        asset_ids = select(Asset.id).where(Asset.secid == secid.strip().upper())
        conditions.append(Transaction.asset_id.in_(asset_ids))
    if search:
        pattern = f"%{search.strip().upper()}%"
        matched = select(Asset.id).where(
            func.upper(Asset.secid).like(pattern) | func.upper(Asset.name).like(pattern)
        )
        conditions.append(Transaction.asset_id.in_(matched))

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = await db.scalar(count_stmt) or 0
    stmt = stmt.order_by(Transaction.executed_at.desc(), Transaction.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    rows = list((await db.scalars(stmt)).all())
    return TransactionPage(
        items=[_serialize(t) for t in rows], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=TransactionOut, status_code=201, summary="Добавить сделку или выплату")
async def create_transaction(payload: TransactionCreate, db: DB):
    portfolio = await db.get(Portfolio, payload.portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")

    asset = None
    if payload.secid:
        async with MoexISS() as iss:
            asset = await get_or_create_asset(db, payload.secid, iss)
            if asset and not asset.is_custom and asset.engine:
                await sync_price_history(db, asset, iss=iss)
                await sync_payment_schedule(db, asset, iss)

            # по облигациям цена и НКД известны бирже — не заставляем вводить руками
            if (
                asset is not None
                and asset.asset_class == AssetClass.BOND.value
                and payload.type in (TxType.BUY, TxType.SELL)
                and (not payload.price or not payload.accrued_interest)
            ):
                quote = await quote_for_date(db, asset, payload.executed_at.date(), iss=iss)
                if not payload.price and quote["price"]:
                    payload.price = quote["price"]
                if not payload.accrued_interest and quote["accrued_interest"] is not None:
                    payload.accrued_interest = quote["accrued_interest"]
    elif payload.type not in NO_ASSET_TYPES:
        raise HTTPException(400, f"Для операции {payload.type.value} нужен тикер (secid)")

    if payload.type == TxType.SELL:
        if asset is None:
            raise HTTPException(400, "Для продажи нужен инструмент")
        await _ensure_sellable(
            db,
            payload.portfolio_id,
            asset,
            float(payload.quantity),
            payload.executed_at,
        )

    amount = payload.amount
    if amount is None:
        amount = payload.price * payload.quantity if payload.quantity else payload.price

    tx = Transaction(
        portfolio_id=payload.portfolio_id,
        asset_id=asset.id if asset else None,
        type=payload.type.value,
        executed_at=payload.executed_at,
        price=payload.price,
        quantity=payload.quantity,
        amount=amount,
        fee=payload.fee,
        tax=payload.tax,
        accrued_interest=payload.accrued_interest,
        currency=payload.currency,
        note=payload.note,
        source="manual",
        do_not_adjust_cash=payload.do_not_adjust_cash,
    )
    db.add(tx)
    await db.commit()

    tx = await db.scalar(
        select(Transaction)
        .options(selectinload(Transaction.asset), selectinload(Transaction.portfolio))
        .where(Transaction.id == tx.id)
    )
    return _serialize(tx)


@router.patch("/{tx_id}", response_model=TransactionOut, summary="Изменить сделку")
async def update_transaction(tx_id: int, payload: TransactionUpdate, db: DB):
    tx = await db.scalar(
        select(Transaction)
        .options(selectinload(Transaction.asset), selectinload(Transaction.portfolio))
        .where(Transaction.id == tx_id)
    )
    if tx is None:
        raise not_found("Сделка")

    data = payload.model_dump(exclude_unset=True)
    if "type" in data and data["type"] is not None:
        data["type"] = TxType(data["type"]).value

    final_type = data.get("type", tx.type)
    changes_order = (
        ("type" in data and data["type"] is not None and data["type"] != tx.type)
        or (
            "quantity" in data
            and float(data["quantity"]) != float(tx.quantity)
        )
        or ("executed_at" in data and data["executed_at"] != tx.executed_at)
    )
    if final_type == TxType.SELL.value and changes_order:
        if tx.asset_id is None:
            raise HTTPException(400, "Для продажи нужен инструмент")
        asset = tx.asset or await db.get(Asset, tx.asset_id)
        if asset is None:
            raise not_found("Актив")
        await _ensure_sellable(
            db,
            tx.portfolio_id,
            asset,
            float(data.get("quantity", tx.quantity)),
            data.get("executed_at", tx.executed_at),
            exclude_id=tx.id,
        )

    for key, value in data.items():
        setattr(tx, key, value)
    if "amount" not in data and ("price" in data or "quantity" in data):
        tx.amount = float(tx.price) * float(tx.quantity)
    await db.commit()
    await db.refresh(tx)
    return _serialize(tx)


@router.delete("/{tx_id}", status_code=204, summary="Удалить сделку")
async def delete_transaction(tx_id: int, db: DB):
    tx = await db.get(Transaction, tx_id)
    if tx is None:
        raise not_found("Сделка")
    await db.delete(tx)
    await db.commit()


@router.delete("", summary="Удалить сделки пачкой")
async def delete_transactions(db: DB, ids: list[int] = Query(...)):
    rows = list((await db.scalars(select(Transaction).where(Transaction.id.in_(ids)))).all())
    for tx in rows:
        await db.delete(tx)
    await db.commit()
    return {"deleted": len(rows)}
