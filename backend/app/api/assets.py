from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import DB, not_found
from app.enums import AssetClass, PaymentKind, PayoutFrequency
from app.models import Asset, PaymentSchedule, PriceHistory, Quote, Transaction
from app.schemas import (
    AssetOut,
    AssetQuoteOut,
    AssetSearchResult,
    AssetUpdate,
    CustomAssetCreate,
)
from app.services.market_data import (
    quote_for_date,
    refresh_asset_reference,
    sync_payment_schedule,
    sync_price_history,
)
from app.services.moex import MoexISS

router = APIRouter(prefix="/assets", tags=["Активы"])


@router.get("", response_model=list[AssetOut], summary="Инструменты в базе")
async def list_assets(
    db: DB,
    asset_class: str | None = None,
    only_held: bool = Query(False, description="Только те, по которым есть сделки"),
):
    stmt = select(Asset).order_by(Asset.secid)
    if asset_class:
        stmt = stmt.where(Asset.asset_class == asset_class.upper())
    if only_held:
        stmt = stmt.where(Asset.id.in_(select(Transaction.asset_id).distinct()))
    return list((await db.scalars(stmt)).all())


@router.get("/search", response_model=list[AssetSearchResult], summary="Поиск инструмента на Мосбирже")
async def search_assets(db: DB, q: str = Query(min_length=2), limit: int = 20):
    known = {a.secid for a in (await db.scalars(select(Asset))).all()}
    async with MoexISS() as iss:
        found = await iss.search(q, limit)
    return [
        AssetSearchResult(
            secid=item["secid"],
            name=item.get("name"),
            short_name=item.get("short_name"),
            isin=item.get("isin"),
            asset_class=item.get("asset_class") or "SHARE",
            board=item.get("board"),
            in_portfolio=item["secid"] in known,
        )
        for item in found
        if item.get("secid")
    ]


@router.get("/quote", response_model=AssetQuoteOut, summary="Цена и НКД на дату")
async def asset_quote(
    db: DB,
    secid: str = Query(min_length=1, description="Тикер инструмента"),
    when: date = Query(date.today(), alias="date", description="Дата сделки"),
):
    """Чистая цена и НКД за единицу — для автозаполнения формы сделки.

    Инструмент может ещё не быть в базе: тогда справочник берётся из ISS,
    но запись в БД не создаётся — это делает только сохранение сделки.
    """
    code = secid.strip().upper()
    asset = await db.scalar(select(Asset).where(Asset.secid == code))

    async with MoexISS() as iss:
        if asset is None:
            info = await iss.security_info(code)
            if info is None:
                raise not_found("Актив")
            view = SimpleNamespace(
                id=0,
                secid=code,
                name=info.get("name") or code,
                short_name=info.get("short_name"),
                asset_class=info.get("asset_class") or AssetClass.SHARE.value,
                engine=info.get("engine"),
                market=info.get("market"),
                board=info.get("board"),
                face_value=info.get("face_value"),
                initial_face_value=info.get("initial_face_value"),
                is_custom=False,
            )
        else:
            view = asset

        quote = await quote_for_date(db, view, when, iss=iss)

    return AssetQuoteOut(
        secid=code,
        name=view.short_name or view.name,
        asset_class=view.asset_class,
        is_bond=view.asset_class == AssetClass.BOND.value,
        price=quote["price"],
        accrued_interest=quote["accrued_interest"],
        as_of=quote["as_of"],
        source=quote["source"],
    )


@router.post("/custom", response_model=AssetOut, status_code=201, summary="Создать кастомный актив")
async def create_custom_asset(payload: CustomAssetCreate, db: DB):
    exists = await db.scalar(select(Asset).where(Asset.secid == payload.secid))
    if exists:
        raise HTTPException(409, f"Актив {payload.secid} уже существует")

    asset = Asset(
        secid=payload.secid,
        name=payload.name,
        short_name=payload.name[:128],
        asset_class=payload.asset_class.value,
        currency=payload.currency,
        is_custom=True,
        manual_price=payload.manual_price,
        annual_rate=payload.annual_rate,
        payout_frequency=payload.payout_frequency.value,
        capitalization=payload.capitalization,
        start_date=payload.start_date or date.today(),
        end_date=payload.end_date,
        meta={"note": payload.note} if payload.note else None,
    )
    db.add(asset)
    await db.flush()
    await _rebuild_custom_schedule(db, asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/{asset_id}", response_model=AssetOut, summary="Карточка инструмента")
async def get_asset(asset_id: int, db: DB):
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise not_found("Актив")
    return asset


@router.patch("/{asset_id}", response_model=AssetOut, summary="Изменить инструмент")
async def update_asset(asset_id: int, payload: AssetUpdate, db: DB):
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise not_found("Актив")
    data = payload.model_dump(exclude_unset=True)
    if "payout_frequency" in data and data["payout_frequency"] is not None:
        data["payout_frequency"] = PayoutFrequency(data["payout_frequency"]).value
    for key, value in data.items():
        setattr(asset, key, value)
    if asset.is_custom:
        await _rebuild_custom_schedule(db, asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=204, summary="Удалить инструмент")
async def delete_asset(asset_id: int, db: DB):
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise not_found("Актив")
    used = await db.scalar(
        select(func.count(Transaction.id)).where(Transaction.asset_id == asset_id)
    )
    if used:
        raise HTTPException(409, f"По активу есть {used} сделок — сначала удалите их")
    await db.delete(asset)
    await db.commit()


@router.get("/{asset_id}/history", summary="История цен инструмента")
async def asset_history(asset_id: int, db: DB, days: int = 365):
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise not_found("Актив")
    since = date.today() - timedelta(days=days)
    rows = (
        await db.execute(
            select(PriceHistory.date, PriceHistory.close_norm, PriceHistory.accrued_interest)
            .where(PriceHistory.asset_id == asset_id, PriceHistory.date >= since)
            .order_by(PriceHistory.date)
        )
    ).all()
    quote = await db.get(Quote, asset_id)
    return {
        "secid": asset.secid,
        "name": asset.short_name or asset.name,
        "currency": asset.currency,
        "points": [
            {
                "date": d.isoformat(),
                "close": float(c),
                "accrued_interest": float(a) if a is not None else None,
            }
            for d, c, a in rows
        ],
        "last": {
            "price": float(quote.price_norm),
            "change_pct": float(quote.change_pct) if quote.change_pct is not None else None,
            "updated_at": quote.updated_at.isoformat(),
            "is_delayed": quote.is_delayed,
        }
        if quote
        else None,
    }


@router.post("/{asset_id}/sync", summary="Обновить данные инструмента с Мосбиржи")
async def sync_asset(asset_id: int, db: DB):
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise not_found("Актив")
    if asset.is_custom:
        raise HTTPException(400, "Кастомный актив не синхронизируется с биржей")
    async with MoexISS() as iss:
        await refresh_asset_reference(db, asset, iss)
        prices = await sync_price_history(db, asset, iss=iss)
        payments = await sync_payment_schedule(db, asset, iss)
    await db.commit()
    return {"secid": asset.secid, "prices_loaded": prices, "payments_loaded": payments}


async def _rebuild_custom_schedule(db: DB, asset: Asset) -> None:
    """Строит календарь выплат кастомного актива по ставке и периодичности."""
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(PaymentSchedule).where(
            PaymentSchedule.asset_id == asset.id,
            PaymentSchedule.kind == PaymentKind.CUSTOM_INCOME.value,
        )
    )
    rate = float(asset.annual_rate or 0)
    if not rate or asset.capitalization:
        return
    freq = PayoutFrequency(asset.payout_frequency or PayoutFrequency.MONTHLY.value)
    per_year = freq.per_year
    if per_year <= 0:
        return

    start = asset.start_date or date.today()
    end = asset.end_date or (date.today() + timedelta(days=365 * 2))
    step_days = round(365 / per_year)
    unit_price = float(asset.manual_price or 1)
    payment_per_unit = unit_price * rate / 100 / per_year

    cursor = start + timedelta(days=step_days)
    rows = []
    while cursor <= end:
        rows.append(
            PaymentSchedule(
                asset_id=asset.id,
                kind=PaymentKind.CUSTOM_INCOME.value,
                payment_date=cursor,
                value_per_unit=payment_per_unit,
                currency=asset.currency,
                is_forecast=cursor > date.today(),
                source="manual",
                note=f"расчёт по ставке {rate:g}% годовых",
            )
        )
        cursor += timedelta(days=step_days)
    db.add_all(rows)
