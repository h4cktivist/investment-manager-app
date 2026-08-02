from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.api.deps import DB, PortfolioIds
from app.config import settings
from app.services.analytics import PortfolioAnalytics
from app.services.portfolio import upcoming_payments_for_holdings

router = APIRouter(prefix="/portfolio", tags=["Аналитика"])

PeriodQuery = Query("ALL", description="1M, 3M, 6M, YTD, 1Y, 3Y, 5Y или ALL")


@router.get("/summary", summary="Сводка по портфелю")
async def summary(db: DB, portfolio_id: PortfolioIds = None):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return analytics.summary()


@router.get("/positions", summary="Текущие позиции")
async def positions(
    db: DB,
    portfolio_id: PortfolioIds = None,
    include_closed: bool = Query(False, description="Показывать закрытые позиции"),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    rows = analytics.positions()
    if not include_closed:
        rows = [r for r in rows if r["is_open"]]
    return rows


@router.get("/allocation", summary="Структура портфеля")
async def allocation(db: DB, portfolio_id: PortfolioIds = None):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return analytics.allocation()


@router.get("/performance", summary="График стоимости и доходности vs бенчмарки")
async def performance(
    db: DB,
    portfolio_id: PortfolioIds = None,
    period: str = PeriodQuery,
    benchmark: list[str] = Query(["IMOEX", "RGBITR", "RUCBTRNS"]),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return await analytics.performance_chart(period=period, benchmark_codes=benchmark)


@router.get("/metrics", summary="Метрики доходности и риска")
async def metrics(
    db: DB,
    portfolio_id: PortfolioIds = None,
    period: str = PeriodQuery,
    benchmark: str = "IMOEX",
    risk_free: float | None = Query(
        None, description="Безрисковая ставка долей, например 0.16. По умолчанию из настроек"
    ),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return await analytics.metrics(period=period, benchmark_code=benchmark, risk_free=risk_free)


@router.get("/metrics/all-periods", summary="Метрики по всем периодам разом")
async def metrics_all_periods(
    db: DB,
    portfolio_id: PortfolioIds = None,
    benchmark: str = "IMOEX",
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    out = {}
    for period in ("1M", "3M", "6M", "YTD", "1Y", "3Y", "ALL"):
        out[period] = await analytics.metrics(period=period, benchmark_code=benchmark)
    return out


@router.get("/bonds", summary="Доходности и дюрация облигаций")
async def bonds(db: DB, portfolio_id: PortfolioIds = None):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return await analytics.bonds()


@router.get("/passive-income", summary="Ожидаемые купоны и дивиденды вперёд")
async def passive_income(
    db: DB,
    portfolio_id: PortfolioIds = None,
    months: int = Query(12, ge=1, le=36),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return await analytics.passive_income(months)


@router.get("/income", summary="Полученные выплаты по месяцам")
async def income(db: DB, portfolio_id: PortfolioIds = None, months: int = 24):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    return {"months": analytics.income_by_month(months)}


@router.get("/dashboard", summary="Всё для дашборда одним запросом")
async def dashboard(
    db: DB,
    portfolio_id: PortfolioIds = None,
    period: str = PeriodQuery,
    benchmark: list[str] = Query(["IMOEX", "RGBITR", "RUCBTRNS"]),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    positions_rows = analytics.positions()
    open_rows = [r for r in positions_rows if r["is_open"]]

    today = date.today()
    upcoming = await upcoming_payments_for_holdings(
        db, analytics.open_quantities(), today, today + timedelta(days=90)
    )

    return {
        "summary": analytics.summary(positions_rows),
        "allocation": analytics.allocation(positions_rows),
        "positions": open_rows[:100],
        "performance": await analytics.performance_chart(period=period, benchmark_codes=benchmark),
        "metrics": await analytics.metrics(period=period, benchmark_code=benchmark[0]),
        "passive_income": await analytics.passive_income(12),
        "income_months": analytics.income_by_month(24),
        "upcoming_payments": [
            {**item, "payment_date": item["payment_date"].isoformat(),
             "record_date": item["record_date"].isoformat() if item["record_date"] else None}
            for item in upcoming[:20]
        ],
        "upcoming_total": sum(item["amount"] for item in upcoming),
        "risk_free_rate": settings.risk_free_rate,
    }
