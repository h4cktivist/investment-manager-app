import logging

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB
from app.config import settings
from app.models import Asset, Benchmark
from app.schemas import SyncRequest, SyncResult
from app.services.market_data import (
    ensure_benchmarks,
    refresh_asset_reference,
    sync_benchmark_history,
    sync_payment_schedule,
    sync_price_history,
)
from app.services.moex import MoexISS
from app.services.realtime import build_provider, hub, refresh_quotes_once

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["Синхронизация"])


@router.post("", response_model=SyncResult, summary="Обновить данные с Мосбиржи")
async def run_sync(payload: SyncRequest, db: DB):
    errors: list[str] = []
    assets_updated = prices_loaded = payments_loaded = benchmarks_updated = 0

    await ensure_benchmarks(db)
    await db.commit()

    stmt = select(Asset).where(Asset.is_custom.is_(False))
    if payload.secids:
        stmt = stmt.where(Asset.secid.in_([s.upper() for s in payload.secids]))
    assets = list((await db.scalars(stmt)).all())

    async with MoexISS() as iss:
        for asset in assets:
            try:
                await refresh_asset_reference(db, asset, iss)
                assets_updated += 1
                if payload.with_history:
                    prices_loaded += await sync_price_history(db, asset, iss=iss)
                if payload.with_schedule:
                    payments_loaded += await sync_payment_schedule(db, asset, iss)
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                errors.append(f"{asset.secid}: {exc}")

        if payload.with_benchmarks:
            benchmarks = list((await db.scalars(select(Benchmark))).all())
            for benchmark in benchmarks:
                try:
                    await sync_benchmark_history(db, benchmark, iss)
                    benchmarks_updated += 1
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    errors.append(f"{benchmark.code}: {exc}")

    quotes = 0
    try:
        quotes = await refresh_quotes_once()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"котировки: {exc}")

    return SyncResult(
        assets_updated=assets_updated,
        prices_loaded=prices_loaded,
        payments_loaded=payments_loaded,
        benchmarks_updated=benchmarks_updated,
        quotes_updated=quotes,
        errors=errors[:50],
    )


@router.post("/quotes", summary="Обновить только котировки")
async def sync_quotes():
    count = await refresh_quotes_once()
    return {"updated": count, "ws_clients": hub.client_count}


@router.get("/status", summary="Состояние источника котировок")
async def sync_status(db: DB):
    provider = build_provider()
    assets = len(list((await db.scalars(select(Asset.id))).all()))
    return {
        "provider": provider.name,
        "is_delayed": provider.is_delayed,
        "poll_seconds": settings.quote_poll_seconds,
        "assets": assets,
        "ws_clients": hub.client_count,
        "cached_quotes": len(hub.last_ticks),
        "note": (
            "MOEX ISS отдаёт данные с задержкой около 15 минут. "
            "Для котировок без задержки укажите TINVEST_TOKEN и QUOTE_PROVIDER=tinvest."
            if provider.is_delayed
            else "Котировки без задержки через T-Invest API."
        ),
    }
