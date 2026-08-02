from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, not_found
from app.enums import TxType
from app.models import Asset, Portfolio, Transaction
from app.schemas import (
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
)
from app.services.market_data import get_or_create_asset, sync_payment_schedule, sync_price_history
from app.services.moex import MoexISS

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
    elif payload.type not in NO_ASSET_TYPES:
        raise HTTPException(400, f"Для операции {payload.type.value} нужен тикер (secid)")

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
