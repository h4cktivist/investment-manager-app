import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

from app.api import (
    analytics_api,
    assets,
    benchmarks,
    calendar_api,
    imports,
    portfolios,
    sync_api,
    transactions,
    ws,
)
from app.config import settings
from app.db import SessionLocal, engine
from app.models import Asset, Benchmark, Transaction
from app.schemas import HealthOut
from app.services.market_data import ensure_benchmarks, sync_benchmark_history
from app.services.moex import MoexISS
from app.services.realtime import build_provider, hub, quote_poller

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")


async def _ensure_benchmarks() -> None:
    """Быстрая часть старта: завести записи индексов, не ходя в сеть."""
    async with SessionLocal() as session:
        await ensure_benchmarks(session)
        await session.commit()


async def _startup_sync() -> None:
    """Подтягивает историю индексов. Выполняется в фоне: ISS бывает медленным,
    и приложение не должно из-за этого висеть в «Waiting for application startup»."""
    if not settings.sync_on_startup:
        return
    async with SessionLocal() as session:
        rows = list((await session.scalars(select(Benchmark))).all())
        async with MoexISS() as iss:
            for benchmark in rows:
                try:
                    loaded = await sync_benchmark_history(session, benchmark, iss)
                    await session.commit()
                    if loaded:
                        log.info("бенчмарк %s: загружено %s точек", benchmark.code, loaded)
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    log.warning("бенчмарк %s: %s", benchmark.code, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks: list[asyncio.Task] = []
    try:
        await _ensure_benchmarks()
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось завести бенчмарки: %s", exc)

    # обе задачи фоновые — API отвечает сразу, данные догружаются следом
    tasks.append(asyncio.create_task(_startup_sync(), name="startup-sync"))
    tasks.append(asyncio.create_task(quote_poller(), name="quote-poller"))
    log.info("API готов, провайдер котировок: %s", build_provider().name)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()


app = FastAPI(
    title="Investment Manager API",
    description=(
        "Учёт инвестиций в инструменты российского рынка: сделки, выплаты, "
        "метрики доходности и риска, облигационные расчёты, сравнение с индексами Мосбиржи."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for api_router in (
    portfolios.router,
    assets.router,
    transactions.router,
    imports.router,
    analytics_api.router,
    calendar_api.router,
    benchmarks.router,
    sync_api.router,
):
    app.include_router(api_router, prefix="/api")

app.include_router(ws.router)


@app.get("/api/health", response_model=HealthOut, tags=["Служебное"], summary="Проверка живости")
async def health() -> HealthOut:
    database = True
    assets_count = tx_count = 0
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            assets_count = await session.scalar(select(func.count(Asset.id))) or 0
            tx_count = await session.scalar(select(func.count(Transaction.id))) or 0
    except Exception as exc:  # noqa: BLE001
        log.warning("health: база недоступна: %s", exc)
        database = False

    provider = build_provider()
    return HealthOut(
        status="ok" if database else "degraded",
        database=database,
        quote_provider=provider.name,
        quote_delayed=provider.is_delayed,
        ws_clients=hub.client_count,
        assets=assets_count,
        transactions=tx_count,
    )
