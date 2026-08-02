from datetime import date, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, PortfolioIds
from app.models import Benchmark, BenchmarkPrice
from app.services.analytics import PortfolioAnalytics, period_start

router = APIRouter(prefix="/benchmarks", tags=["Бенчмарки"])

KIND_LABELS = {
    "EQUITY": "Акции",
    "BOND_GOV": "Госбумаги",
    "BOND_CORP": "Корпоративные облигации",
    "MONEY": "Денежный рынок",
    "MIXED": "Смешанный",
}


@router.get("", summary="Доступные индексы Мосбиржи")
async def list_benchmarks(db: DB):
    rows = list((await db.scalars(select(Benchmark).order_by(Benchmark.id))).all())
    out = []
    for benchmark in rows:
        last = await db.scalar(
            select(BenchmarkPrice)
            .where(BenchmarkPrice.benchmark_id == benchmark.id)
            .order_by(BenchmarkPrice.date.desc())
            .limit(1)
        )
        out.append(
            {
                "id": benchmark.id,
                "code": benchmark.code,
                "name": benchmark.name,
                "kind": benchmark.kind,
                "kind_label": KIND_LABELS.get(benchmark.kind, benchmark.kind),
                "is_default": benchmark.is_default,
                "last_date": last.date.isoformat() if last else None,
                "last_value": float(last.close) if last else None,
            }
        )
    return out


@router.get("/compare", summary="Сравнить портфель с индексами")
async def compare(
    db: DB,
    portfolio_id: PortfolioIds = None,
    period: str = Query("ALL"),
    codes: list[str] = Query(["IMOEX", "MCFTR", "RGBITR", "RUCBTRNS"]),
):
    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    chart = await analytics.performance_chart(period=period, benchmark_codes=codes)

    rows = []
    portfolio_return = chart.get("portfolio_return_pct")
    for item in chart.get("benchmarks", []):
        bench_return = item.get("total_return_pct")
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "kind": item["kind"],
                "kind_label": KIND_LABELS.get(item["kind"], item["kind"]),
                "return_pct": bench_return,
                "excess_pct": (
                    round(portfolio_return - bench_return, 4)
                    if portfolio_return is not None and bench_return is not None
                    else None
                ),
            }
        )

    metrics = await analytics.metrics(period=period, benchmark_code=codes[0] if codes else "IMOEX")
    return {
        "period": period,
        "portfolio_return_pct": portfolio_return,
        "benchmarks": rows,
        "chart": chart,
        "beta": metrics.get("beta"),
        "alpha": metrics.get("alpha"),
        "correlation": metrics.get("correlation"),
    }


@router.get("/{code}/history", summary="История индекса")
async def benchmark_history(code: str, db: DB, period: str = "ALL"):
    benchmark = await db.scalar(select(Benchmark).where(Benchmark.code == code.upper()))
    if benchmark is None:
        return {"code": code, "points": []}
    start = period_start(period, date(2013, 1, 1)) or date(2013, 1, 1)
    rows = (
        await db.execute(
            select(BenchmarkPrice.date, BenchmarkPrice.close)
            .where(BenchmarkPrice.benchmark_id == benchmark.id, BenchmarkPrice.date >= start)
            .order_by(BenchmarkPrice.date)
        )
    ).all()
    return {
        "code": benchmark.code,
        "name": benchmark.name,
        "points": [{"date": d.isoformat(), "close": float(c)} for d, c in rows],
    }
