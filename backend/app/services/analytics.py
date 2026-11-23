"""Сборка витрин: позиции, дашборд, метрики, облигации, сравнение с бенчмарками."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import AssetClass, PaymentKind
from app.models import (
    Asset,
    Benchmark,
    BenchmarkPrice,
    PaymentSchedule,
    Portfolio,
    PriceHistory,
    Quote,
)
from app.services.portfolio import upcoming_payments_for_holdings
from app.services import bond_math, metrics as m
from app.services.moex import MoexISS, normalize_price
from app.services.portfolio import (
    EPS,
    LedgerResult,
    PriceBook,
    ValuationPoint,
    build_valuation_series,
    f,
    load_price_book,
    load_transactions,
    replay,
    resample_to_trading_days,
)

log = logging.getLogger(__name__)

PERIODS = {
    "1M": 30,
    "3M": 90,
    "6M": 182,
    "1Y": 365,
    "3Y": 1095,
    "5Y": 1826,
}

#: на сколько дней заглядываем назад за стоимостью портфеля на начало периода
LOOKBACK_DAYS = 14
#: для корреляций нужен запас цены перед началом периода, чтобы посчитать
#: доходность первого дня окна
CORRELATION_LOOKBACK_DAYS = 14
#: меньше 10 общих доходностей — корреляция на таком окне не имеет смысла
CORRELATION_MIN_RETURNS = 10
#: больше 15 бумаг матрица перестаёт читаться на экране
CORRELATION_MAX_ASSETS = 15

ASSET_CLASS_LABELS = {
    "SHARE": "Акции",
    "BOND": "Облигации",
    "ETF": "Фонды",
    "CURRENCY": "Валюта",
    "DEPOSIT": "Вклады",
    "CUSTOM": "Прочее",
}


def period_start(period: str, first_day: date | None) -> date | None:
    period = (period or "ALL").upper()
    today = date.today()
    if period == "ALL":
        return first_day
    if period == "YTD":
        start = date(today.year, 1, 1)
    elif period in PERIODS:
        start = today - timedelta(days=PERIODS[period])
    else:
        return first_day
    if first_day and start < first_day:
        return first_day
    return start


class PortfolioAnalytics:
    """Один расчётный проход по портфелю, из которого собираются все витрины."""

    def __init__(
        self,
        session: AsyncSession,
        ledger: LedgerResult,
        assets: dict[int, Asset],
        prices: PriceBook,
        quotes: dict[int, Quote],
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.assets = assets
        self.prices = prices
        self.quotes = quotes

    @classmethod
    async def load(
        cls, session: AsyncSession, portfolio_ids: list[int] | None = None
    ) -> PortfolioAnalytics:
        transactions = await load_transactions(session, portfolio_ids)
        ledger = replay(transactions)
        asset_ids = sorted({t.asset_id for t in transactions if t.asset_id is not None})
        assets = {
            a.id: a
            for a in (await session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))).all()
        }
        prices = await load_price_book(session, asset_ids)
        quotes = {
            q.asset_id: q
            for q in (await session.scalars(select(Quote).where(Quote.asset_id.in_(asset_ids)))).all()
        }
        return cls(session, ledger, assets, prices, quotes)

    # ------------------------------------------------------------- позиции

    def positions(self) -> list[dict]:
        out = []
        for asset_id, pos in self.ledger.positions.items():
            asset = self.assets.get(asset_id)
            if asset is None:
                continue
            price = self.prices.latest(asset_id)
            has_price = bool(price)
            if not has_price:
                # биржа не дала котировку — показываем позицию по себестоимости
                price = pos.avg_price
            quote = self.quotes.get(asset_id)
            face = f(asset.face_value or asset.initial_face_value) or None

            accrued = f(quote.accrued_interest) if quote and quote.accrued_interest else 0.0
            clean_price = price - accrued if accrued else price
            market_value = pos.qty * price
            cost_basis = pos.cost_basis
            unrealized = market_value - cost_basis

            prev_norm = None
            if quote and quote.prev_close is not None:
                prev_norm = normalize_price(asset.asset_class, f(quote.prev_close), face)
            if prev_norm is None:
                prev_norm = self.prices.previous_clean(asset_id, clean_price)
            # Как в брокерском отчёте: изменение только по чистой цене.
            # НКД начисляется каждый день и в дневное изменение не входит —
            # иначе перед купоном («обнуление» НКД) получается ложный минус.
            day_change = (clean_price - prev_norm) * pos.qty if prev_norm else 0.0
            day_change_pct = (
                clean_price / prev_norm - 1.0 if prev_norm and prev_norm > EPS else None
            )
            if day_change_pct is None and quote and quote.change_pct is not None:
                day_change_pct = f(quote.change_pct) / 100

            is_open = pos.qty > EPS
            out.append(
                {
                    "asset_id": asset_id,
                    "secid": asset.secid,
                    "name": asset.short_name or asset.name,
                    "full_name": asset.name,
                    "asset_class": asset.asset_class,
                    "asset_class_label": ASSET_CLASS_LABELS.get(asset.asset_class, asset.asset_class),
                    "currency": asset.currency,
                    "is_open": is_open,
                    "quantity": pos.qty,
                    "lot_size": asset.lot_size,
                    "avg_price": pos.avg_price,
                    "avg_clean_price": pos.avg_clean_price,
                    "last_price": price,
                    "clean_price": clean_price,
                    "accrued_interest": accrued,
                    "cost_basis": cost_basis,
                    "market_value": market_value,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_pct": (unrealized / cost_basis) if cost_basis > EPS else None,
                    "realized_pnl": pos.realized_pnl,
                    "dividends": pos.dividends,
                    "coupons": pos.coupons,
                    "amortization": pos.amortization,
                    "income": pos.income + pos.amortization,
                    "fees": pos.fees,
                    "taxes": pos.taxes,
                    "total_pnl": unrealized + pos.realized_pnl + pos.income + pos.amortization,
                    "day_change": day_change,
                    "day_change_pct": day_change_pct,
                    "quote_updated_at": quote.updated_at.isoformat() if quote else None,
                    "quote_delayed": quote.is_delayed if quote else None,
                    "has_market_price": has_price,
                    "maturity_date": asset.maturity_date.isoformat() if asset.maturity_date else None,
                    "first_trade": pos.first_trade.isoformat() if pos.first_trade else None,
                    "is_custom": asset.is_custom,
                }
            )

        total_value = sum(p["market_value"] for p in out if p["is_open"])
        for p in out:
            p["weight"] = (p["market_value"] / total_value) if total_value > EPS and p["is_open"] else 0.0
        out.sort(key=lambda p: (-p["market_value"], p["secid"]))
        return out

    # -------------------------------------------------------------- сводка

    def summary(self, positions: list[dict] | None = None) -> dict:
        positions = positions if positions is not None else self.positions()
        open_positions = [p for p in positions if p["is_open"]]
        market_value = sum(p["market_value"] for p in open_positions)
        cost_basis = sum(p["cost_basis"] for p in open_positions)
        unrealized = market_value - cost_basis
        income = self.ledger.dividends + self.ledger.coupons + self.ledger.interest
        total_value = market_value + self.ledger.cash
        net_invested = self.ledger.total_deposits - self.ledger.total_withdrawals

        return {
            "market_value": market_value,
            "cash": self.ledger.cash,
            "total_value": total_value,
            "cost_basis": cost_basis,
            "net_invested": net_invested,
            "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": (unrealized / cost_basis) if cost_basis > EPS else None,
            "realized_pnl": self.ledger.realized_pnl,
            "dividends": self.ledger.dividends,
            "coupons": self.ledger.coupons,
            "interest": self.ledger.interest,
            "amortization": self.ledger.amortization,
            "income": income,
            "fees": self.ledger.total_fees,
            "taxes": self.ledger.total_taxes,
            "total_pnl": total_value - net_invested,
            "total_pnl_pct": (total_value - net_invested) / net_invested if net_invested > EPS else None,
            "day_change": sum(p["day_change"] for p in open_positions),
            "positions_count": len(open_positions),
            "first_transaction": self.ledger.start_date.isoformat() if self.ledger.start_date else None,
        }

    # ------------------------------------------------------------ аллокация

    def allocation(self, positions: list[dict] | None = None) -> dict:
        positions = positions if positions is not None else self.positions()
        open_positions = [p for p in positions if p["is_open"]]
        total = sum(p["market_value"] for p in open_positions) + max(self.ledger.cash, 0.0)

        by_class: dict[str, float] = defaultdict(float)
        for p in open_positions:
            by_class[p["asset_class"]] += p["market_value"]
        if self.ledger.cash > EPS:
            by_class["CASH"] += self.ledger.cash

        def to_list(mapping: dict[str, float]) -> list[dict]:
            items = [
                {
                    "key": key,
                    "label": ASSET_CLASS_LABELS.get(key, "Деньги" if key == "CASH" else key),
                    "value": value,
                    "weight": value / total if total > EPS else 0.0,
                }
                for key, value in mapping.items()
                if abs(value) > EPS
            ]
            items.sort(key=lambda x: -x["value"])
            return items

        by_asset = [
            {
                "key": p["secid"],
                "label": p["name"],
                "asset_class": p["asset_class"],
                "value": p["market_value"],
                "weight": p["market_value"] / total if total > EPS else 0.0,
            }
            for p in open_positions
        ]
        by_asset.sort(key=lambda x: -x["value"])

        return {"by_class": to_list(by_class), "by_asset": by_asset, "total": total}

    # -------------------------------------------------- временные ряды и метрики

    def valuation(self, start: date | None = None, end: date | None = None) -> list[ValuationPoint]:
        return build_valuation_series(self.ledger, self.prices, start=start, end=end)

    async def valuation_with_baseline(
        self, start: date, end: date
    ) -> tuple[float | None, list[ValuationPoint]]:
        """Ряд переоценки за период + стоимость портфеля на день перед его началом.

        Без базы доходность за короткий период считалась бы от первого платежа
        внутри окна, а не от уже накопленного капитала.
        """
        first = self.ledger.start_date
        lookback = max(start - timedelta(days=LOOKBACK_DAYS), first) if first else start
        raw = build_valuation_series(self.ledger, self.prices, start=lookback, end=end)
        if not raw:
            return None, []

        trading = await self.trading_days(lookback, end)
        points = resample_to_trading_days(raw, trading) if trading else raw

        before = [p for p in points if p.day < start]
        inside = [p for p in points if p.day >= start]
        if not inside:
            inside = points[-1:]
            before = points[:-1]
        baseline = before[-1].total if before else None
        return baseline, inside

    async def trading_days(self, start: date, end: date) -> set[date]:
        rows = await self.session.execute(
            select(BenchmarkPrice.date)
            .join(Benchmark, Benchmark.id == BenchmarkPrice.benchmark_id)
            .where(Benchmark.code == "IMOEX", BenchmarkPrice.date >= start, BenchmarkPrice.date <= end)
        )
        return {r[0] for r in rows}

    async def benchmark_series(
        self, code: str, start: date, end: date
    ) -> tuple[Benchmark | None, dict[date, float]]:
        benchmark = await self.session.scalar(select(Benchmark).where(Benchmark.code == code))
        if benchmark is None:
            return None, {}
        rows = await self.session.execute(
            select(BenchmarkPrice.date, BenchmarkPrice.close)
            .where(
                BenchmarkPrice.benchmark_id == benchmark.id,
                BenchmarkPrice.date >= start - timedelta(days=10),
                BenchmarkPrice.date <= end,
            )
            .order_by(BenchmarkPrice.date)
        )
        return benchmark, {d: f(c) for d, c in rows}

    async def metrics(
        self,
        period: str = "ALL",
        benchmark_code: str = "IMOEX",
        risk_free: float | None = None,
    ) -> dict:
        risk_free = settings.risk_free_rate if risk_free is None else risk_free
        first = self.ledger.start_date
        if first is None:
            return {"available": False, "reason": "нет сделок"}

        start = period_start(period, first) or first
        end = date.today()
        baseline, points = await self.valuation_with_baseline(start, end)
        if len(points) < 2:
            return {"available": False, "reason": "недостаточно истории"}

        series = m.twr_returns(points, opening_value=baseline)
        benchmark, bench_prices = await self.benchmark_series(benchmark_code, start, end)
        bench_daily: list[float] = []
        bench_total: float | None = None
        if bench_prices:
            aligned = _align_benchmark(series.dates, bench_prices)
            bench_daily = aligned["daily"]
            bench_total = aligned["total"]

        flows = [(p.day, p.external_flow) for p in points if abs(p.external_flow) > EPS]
        final_value = points[-1].total

        bundle = m.compute_metrics(
            points,
            flows,
            final_value,
            risk_free,
            benchmark_daily=bench_daily or None,
            benchmark_code=benchmark.code if benchmark else None,
            benchmark_total=bench_total,
            opening_value=baseline,
        )
        data = asdict(bundle)
        data["available"] = True
        data["period"] = period
        data["benchmark_name"] = benchmark.name if benchmark else None
        data["excess_return"] = (
            bundle.twr - bench_total if bench_total is not None else None
        )
        for key in (
            "start",
            "end",
            "max_drawdown_start",
            "max_drawdown_end",
            "worst_day_date",
            "best_day_date",
        ):
            if data.get(key):
                data[key] = data[key].isoformat()
        return data

    # ------------------------------------------------- симуляция Монте-Карло

    async def monte_carlo(
        self, period: str = "ALL", horizon: str = "1Y", simulations: int = 1000
    ) -> dict:
        """Сценарный прогноз стоимости портфеля на выбранный горизонт.

        Опирается на дневные доходности TWR за историческое окно: пополнения
        и выводы уже исключены, поэтому распределение отражает именно рыночный
        результат, а не размер взносов. Это не предсказание, а диапазон
        правдоподобных исходов при сохранении текущей структуры портфеля.
        """
        first = self.ledger.start_date
        if first is None:
            return {"available": False, "reason": "нет сделок"}

        start = period_start(period, first) or first
        end = date.today()
        baseline, points = await self.valuation_with_baseline(start, end)
        if len(points) < 2:
            return {"available": False, "reason": "недостаточно истории"}

        series = m.twr_returns(points, opening_value=baseline)
        horizon = (horizon or "1Y").upper()
        horizon_days = m.MC_HORIZONS.get(horizon, m.MC_HORIZONS["1Y"])
        forecast = m.monte_carlo_forecast(
            series.daily,
            points[-1].total,
            horizon_days,
            simulations=simulations,
        )
        if forecast is None:
            return {
                "available": False,
                "reason": (
                    "для симуляции нужно не меньше "
                    f"{m.MIN_MONTE_CARLO_OBSERVATIONS} торговых дней истории "
                    "и ненулевая стоимость портфеля"
                ),
            }

        dates = _future_business_days(end, horizon_days)
        for point in forecast["points"]:
            day = point["day"]
            point["date"] = end.isoformat() if day == 0 else dates[day - 1].isoformat()

        forecast.update(
            {
                "available": True,
                "period": period,
                "horizon": horizon,
                "method": "бутстрэп исторических дневных доходностей TWR",
                "start": points[0].day.isoformat(),
                "end": end.isoformat(),
            }
        )
        return forecast

    # ------------------------------------------------------- корреляция активов

    async def correlation_matrix(
        self, period: str = "ALL", max_assets: int = CORRELATION_MAX_ASSETS
    ) -> dict:
        """Попарная корреляция и ковариация дневных доходностей открытых позиций.

        Доходности считаются по чистым ценам: у облигаций грязная цена прыгает
        вниз на купонной дате, и такая «доходность» исказила бы корреляцию.
        Цена протягивается вперёд на общей сетке торговых дней Мосбиржи, поэтому
        бумаги с разной историей и пропусками торгов сопоставимы по датам.
        """
        first = self.ledger.start_date
        if first is None:
            return {"available": False, "reason": "нет сделок"}

        start = period_start(period, first) or first
        end = date.today()

        positions = [
            p
            for p in self.positions()
            if p["is_open"]
            and p["has_market_price"]
            and not p["is_custom"]
            and p["market_value"] > EPS
        ]
        if len(positions) < 2:
            return {
                "available": False,
                "reason": "нужны минимум две открытые позиции с рыночной ценой",
            }

        positions.sort(key=lambda p: -p["market_value"])
        total_assets = len(positions)
        limit = max(2, min(max_assets, 30))
        selected = positions[:limit]
        asset_ids = [p["asset_id"] for p in selected]

        lookback = max(start - timedelta(days=CORRELATION_LOOKBACK_DAYS), first)
        trading = await self.trading_days(lookback, end)
        if trading:
            grid = sorted(day for day in trading if lookback <= day <= end)
        else:
            # бенчмарк ещё не подгрузился — берём объединение дат истории цен
            date_rows = await self.session.execute(
                select(PriceHistory.date)
                .where(
                    PriceHistory.asset_id.in_(asset_ids),
                    PriceHistory.date >= lookback,
                    PriceHistory.date <= end,
                )
                .distinct()
            )
            grid = sorted({row[0] for row in date_rows})
        if not grid:
            return {"available": False, "reason": "нет истории цен за период"}

        price_rows = await self.session.execute(
            select(
                PriceHistory.asset_id,
                PriceHistory.date,
                PriceHistory.close_norm,
            )
            .where(
                PriceHistory.asset_id.in_(asset_ids),
                PriceHistory.date >= lookback,
                PriceHistory.date <= end,
            )
            .order_by(PriceHistory.asset_id, PriceHistory.date)
        )
        prices: dict[int, list[tuple[date, float]]] = defaultdict(list)
        for asset_id, day, close in price_rows:
            value = f(close)
            if value > EPS:
                prices[asset_id].append((day, value))

        by_id = {p["asset_id"]: p for p in selected}
        returns: dict[int, list[tuple[date, float]]] = {}
        included: list[dict] = []
        for asset_id in asset_ids:
            series = _returns_on_grid(prices.get(asset_id, []), grid)
            series = [(day, value) for day, value in series if day >= start]
            if len(series) < CORRELATION_MIN_RETURNS:
                continue
            position = by_id[asset_id]
            returns[asset_id] = series
            included.append(
                {
                    "asset_id": asset_id,
                    "secid": position["secid"],
                    "name": position["name"],
                    "asset_class": position["asset_class"],
                    "asset_class_label": position["asset_class_label"],
                    "weight": position["weight"],
                    "market_value": position["market_value"],
                    "observations": len(series),
                    "volatility": m.volatility([value for _, value in series]),
                }
            )

        if len(included) < 2:
            return {
                "available": False,
                "reason": (
                    "нужны минимум две бумаги с историей "
                    f"от {CORRELATION_MIN_RETURNS} торговых дней"
                ),
            }

        maps = {item["asset_id"]: dict(returns[item["asset_id"]]) for item in included}
        count = len(included)
        matrix: list[list[float | None]] = [[None] * count for _ in range(count)]
        pairs: list[dict] = []
        for i in range(count):
            matrix[i][i] = 1.0
            left = maps[included[i]["asset_id"]]
            for j in range(i + 1, count):
                right = maps[included[j]["asset_id"]]
                common = sorted(set(left) & set(right))
                if len(common) < CORRELATION_MIN_RETURNS:
                    continue
                stats = m.pair_statistics(
                    [left[day] for day in common],
                    [right[day] for day in common],
                )
                if stats is None:
                    continue
                matrix[i][j] = matrix[j][i] = stats["correlation"]
                pairs.append(
                    {
                        "i": i,
                        "j": j,
                        "correlation": stats["correlation"],
                        "covariance": stats["covariance"],
                        "covariance_annual": stats["covariance_annual"],
                        # проценты в квадрате: 0,84 %² понятнее, чем 0,000084
                        "covariance_pct2": stats["covariance"] * 10_000,
                        "covariance_annual_pct2": stats["covariance_annual"] * 10_000,
                        "observations": stats["observations"],
                        "start": common[0].isoformat(),
                        "end": common[-1].isoformat(),
                    }
                )

        return {
            "available": True,
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "method": "дневные доходности чистых цен на общей сетке торговых дней",
            "assets": included,
            "correlation": matrix,
            "pairs": pairs,
            "total_assets": total_assets,
            "truncated": total_assets > len(selected),
            "excluded_no_history": len(selected) - len(included),
            "min_observations": CORRELATION_MIN_RETURNS,
        }

    async def performance_chart(
        self, period: str = "ALL", benchmark_codes: list[str] | None = None
    ) -> dict:
        """Ряд стоимости портфеля + сравнение накопленной доходности с бенчмарками."""
        first = self.ledger.start_date
        if first is None:
            return {"points": [], "benchmarks": [], "period": period}

        start = period_start(period, first) or first
        end = date.today()
        baseline, chart_points = await self.valuation_with_baseline(start, end)
        if not chart_points:
            return {"points": [], "benchmarks": [], "period": period}

        series = m.twr_returns(chart_points, opening_value=baseline)

        rows = [
            {
                "date": p.day.isoformat(),
                "value": round(p.value, 2),
                "cash": round(p.cash, 2),
                "total": round(p.total, 2),
                "invested": round(p.invested, 2),
                "return_pct": round((series.index[i] - 1.0) * 100, 4),
                "flow": round(p.external_flow, 2),
            }
            for i, p in enumerate(chart_points)
        ]

        benchmarks = []
        for code in benchmark_codes or ["IMOEX"]:
            benchmark, prices = await self.benchmark_series(code, start, end)
            if not benchmark or not prices:
                continue
            aligned = _align_benchmark(series.dates, prices)
            benchmarks.append(
                {
                    "code": benchmark.code,
                    "name": benchmark.name,
                    "kind": benchmark.kind,
                    "total_return_pct": round(aligned["total"] * 100, 4) if aligned["total"] is not None else None,
                    "points": [
                        {"date": d.isoformat(), "return_pct": round(v * 100, 4)}
                        for d, v in zip(series.dates, aligned["cumulative"])
                    ],
                }
            )

        return {
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "points": rows,
            "benchmarks": benchmarks,
            "portfolio_return_pct": round(series.total * 100, 4),
        }

    def income_by_month(self, months: int = 24) -> list[dict]:
        """Полученные выплаты по месяцам (дивиденды, купоны, амортизация)."""
        buckets: dict[str, dict[str, float]] = {}
        for snap in self.ledger.snapshots:
            if abs(snap.income) <= EPS:
                continue
            key = snap.day.strftime("%Y-%m")
            buckets.setdefault(key, {"month": key, "amount": 0.0})
            buckets[key]["amount"] += snap.income
        rows = sorted(buckets.values(), key=lambda x: x["month"])
        return rows[-months:] if months else rows

    # ----------------------------------------------------------- облигации

    async def bonds(self) -> dict:
        """Доходности и дюрация по каждой облигации в портфеле."""
        today = date.today()
        bond_positions = [
            (asset_id, pos)
            for asset_id, pos in self.ledger.positions.items()
            if pos.qty > EPS
            and (asset := self.assets.get(asset_id)) is not None
            and asset.asset_class == AssetClass.BOND.value
        ]
        if not bond_positions:
            return {"items": [], "summary": bond_math.portfolio_bond_summary([])}

        asset_ids = [aid for aid, _ in bond_positions]
        schedule_rows = (
            await self.session.scalars(
                select(PaymentSchedule).where(
                    PaymentSchedule.asset_id.in_(asset_ids),
                    PaymentSchedule.kind.in_(
                        [
                            PaymentKind.COUPON.value,
                            PaymentKind.AMORTIZATION.value,
                            PaymentKind.MATURITY.value,
                        ]
                    ),
                )
            )
        ).all()
        by_asset: dict[int, list[PaymentSchedule]] = defaultdict(list)
        for row in schedule_rows:
            by_asset[row.asset_id].append(row)

        items = []
        weighted_input = []
        for asset_id, pos in bond_positions:
            asset = self.assets[asset_id]
            quote = self.quotes.get(asset_id)
            face = f(asset.face_value or asset.initial_face_value) or 1000.0

            accrued = f(quote.accrued_interest) if quote and quote.accrued_interest else 0.0
            dirty = self.prices.latest(asset_id) or pos.avg_price
            clean = dirty - accrued if dirty else 0.0
            if clean <= EPS:
                clean = face

            flows = [
                bond_math.CashFlow(
                    date=row.payment_date,
                    amount=f(row.value_per_unit),
                    kind=row.kind,
                )
                for row in by_asset.get(asset_id, [])
            ]
            has_forecast = any(row.is_forecast for row in by_asset.get(asset_id, []))
            coupons_per_year = None
            if asset.coupon_period:
                coupons_per_year = max(round(365 / asset.coupon_period), 1)

            y = bond_math.compute_bond_yields(
                flows=flows,
                clean_price=clean,
                accrued_interest=accrued,
                face_value=face,
                as_of=today,
                maturity_date=asset.maturity_date,
                coupons_per_year=coupons_per_year,
                has_forecast=has_forecast,
            )

            market_value = pos.qty * (clean + accrued)
            next_payment = min(
                (row for row in by_asset.get(asset_id, []) if row.payment_date > today),
                key=lambda r: r.payment_date,
                default=None,
            )

            items.append(
                {
                    "asset_id": asset_id,
                    "secid": asset.secid,
                    "name": asset.short_name or asset.name,
                    "quantity": pos.qty,
                    "face_value": face,
                    "clean_price": clean,
                    "clean_price_pct": clean / face * 100 if face else None,
                    "accrued_interest": accrued,
                    "dirty_price": y.dirty_price,
                    "market_value": market_value,
                    "avg_price": pos.avg_price,
                    "current_yield": bond_math.sanitize(y.current_yield),
                    "simple_yield": bond_math.sanitize(y.simple_yield),
                    "effective_yield": bond_math.sanitize(y.effective_yield),
                    "macaulay_duration_years": bond_math.sanitize(y.macaulay_duration_years),
                    "duration_days": y.macaulay_duration_days,
                    "modified_duration": bond_math.sanitize(y.modified_duration),
                    "days_to_maturity": y.days_to_maturity,
                    "maturity_date": asset.maturity_date.isoformat() if asset.maturity_date else None,
                    "offer_date": asset.offer_date.isoformat() if asset.offer_date else None,
                    "coupon_rate": f(asset.coupon_rate) or None,
                    "annual_coupon": y.annual_coupon,
                    "coupons_per_year": coupons_per_year,
                    "coupon_period_days": asset.coupon_period,
                    "is_estimate": y.is_estimate,
                    "next_payment_date": next_payment.payment_date.isoformat() if next_payment else None,
                    "next_payment_value": f(next_payment.value_per_unit) if next_payment else None,
                    "unrealized_pnl": market_value - pos.cost_basis,
                }
            )
            weighted_input.append((y, market_value))

        items.sort(key=lambda x: -x["market_value"])
        return {
            "items": items,
            "summary": bond_math.portfolio_bond_summary(weighted_input),
        }

    # -------------------------------------------------------- пассивный доход

    async def passive_income(self, months: int = 12) -> dict:
        """Ожидаемые купоны и дивиденды по текущим позициям на горизонте вперёд.

        Амортизация и погашение сюда не входят: это возврат тела долга, а не доход.
        Дивиденды учитываются только объявленные — по большинству акций выплаты
        на год вперёд ещё не утверждены, поэтому цифра по акциям занижена.
        """
        today = date.today()
        horizon = today + timedelta(days=round(365 / 12 * months))
        quantities = self.open_quantities()
        rows = await upcoming_payments_for_holdings(self.session, quantities, today, horizon)

        income_kinds = {
            PaymentKind.COUPON.value,
            PaymentKind.DIVIDEND.value,
            PaymentKind.CUSTOM_INCOME.value,
        }
        principal_kinds = {PaymentKind.AMORTIZATION.value, PaymentKind.MATURITY.value}

        income_rows = [r for r in rows if r["kind"] in income_kinds]
        by_kind: dict[str, float] = defaultdict(float)
        for row in rows:
            by_kind[row["kind"]] += row["amount"]

        total = sum(r["amount"] for r in income_rows)
        principal = sum(by_kind[k] for k in principal_kinds)
        forecast = sum(r["amount"] for r in income_rows if r["is_forecast"])

        by_month: dict[str, dict] = {}
        cursor = today.replace(day=1)
        for _ in range(months + 1):
            by_month[cursor.strftime("%Y-%m")] = {
                "month": cursor.strftime("%Y-%m"),
                "amount": 0.0,
                "count": 0,
            }
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        for row in income_rows:
            key = row["payment_date"].strftime("%Y-%m")
            bucket = by_month.setdefault(key, {"month": key, "amount": 0.0, "count": 0})
            bucket["amount"] += row["amount"]
            bucket["count"] += 1

        positions = {p["asset_id"]: p for p in self.positions() if p["is_open"]}
        market_value = sum(p["market_value"] for p in positions.values())
        cost_basis = sum(p["cost_basis"] for p in positions.values())

        per_asset: dict[int, dict] = {}
        for row in income_rows:
            item = per_asset.setdefault(
                row["asset_id"],
                {
                    "asset_id": row["asset_id"],
                    "secid": row["secid"],
                    "name": row["name"],
                    "asset_class": row["asset_class"],
                    "amount": 0.0,
                    "payments": 0,
                    "is_forecast": False,
                },
            )
            item["amount"] += row["amount"]
            item["payments"] += 1
            item["is_forecast"] = item["is_forecast"] or row["is_forecast"]

        for item in per_asset.values():
            position = positions.get(item["asset_id"])
            value = position["market_value"] if position else 0.0
            item["market_value"] = value
            item["yield_on_position"] = (item["amount"] / value) if value > EPS else None
            item["share"] = (item["amount"] / total) if total > EPS else 0.0

        top = sorted(per_asset.values(), key=lambda x: -x["amount"])

        return {
            "horizon_months": months,
            "date_from": today.isoformat(),
            "date_to": horizon.isoformat(),
            "total": total,
            "coupons": by_kind[PaymentKind.COUPON.value],
            "dividends": by_kind[PaymentKind.DIVIDEND.value],
            "other_income": by_kind[PaymentKind.CUSTOM_INCOME.value],
            "principal_repayments": principal,
            "per_month": total / months if months else 0.0,
            "payments_count": len(income_rows),
            "market_value": market_value,
            "cost_basis": cost_basis,
            "yield_on_market": (total / market_value) if market_value > EPS else None,
            "yield_on_cost": (total / cost_basis) if cost_basis > EPS else None,
            "forecast_amount": forecast,
            "forecast_share": (forecast / total) if total > EPS else 0.0,
            "by_month": [by_month[k] for k in sorted(by_month)][: months + 1],
            "top_assets": top,
        }

    # ------------------------------------------------------------- календарь

    def open_quantities(self) -> dict[int, float]:
        return {aid: p.qty for aid, p in self.ledger.positions.items() if p.qty > EPS}


def _future_business_days(start: date, count: int) -> list[date]:
    """Будущие рабочие дни: выходные пропускаем, праздники не учитываем."""
    days: list[date] = []
    cursor = start
    while len(days) < count:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return days


def _returns_on_grid(
    prices: list[tuple[date, float]], days: list[date]
) -> list[tuple[date, float]]:
    """Дневные доходности актива на общей сетке торговых дней.

    Последняя известная цена протягивается вперёд: если бумага не торговалась,
    доходность дня нулевая до следующей сделки. После первой цены ряд непрерывен,
    поэтому пары активов можно сопоставлять по датам без сдвигов.
    """
    out: list[tuple[date, float]] = []
    idx = 0
    last: float | None = None
    prev: float | None = None
    for day in days:
        while idx < len(prices) and prices[idx][0] <= day:
            last = prices[idx][1]
            idx += 1
        if last is None:
            continue
        if prev is not None and prev > EPS:
            out.append((day, last / prev - 1.0))
        prev = last
    return out


def _align_benchmark(dates: list[date], prices: dict[date, float]) -> dict:
    """Протягивает котировки индекса на сетку дат портфеля и считает доходности."""
    sorted_days = sorted(prices)
    if not sorted_days or not dates:
        return {"daily": [], "cumulative": [], "total": None}

    values: list[float] = []
    idx = 0
    last = prices[sorted_days[0]]
    for day in dates:
        while idx < len(sorted_days) and sorted_days[idx] <= day:
            last = prices[sorted_days[idx]]
            idx += 1
        values.append(last)

    base = values[0]
    daily = [0.0]
    for i in range(1, len(values)):
        prev = values[i - 1]
        daily.append((values[i] / prev - 1.0) if prev > EPS else 0.0)
    cumulative = [(v / base - 1.0) if base > EPS else 0.0 for v in values]
    return {"daily": daily, "cumulative": cumulative, "total": cumulative[-1] if cumulative else None}


async def portfolio_list_with_values(session: AsyncSession) -> list[dict]:
    """Список портфелей с текущей оценкой каждого."""
    portfolios = (
        await session.scalars(select(Portfolio).order_by(Portfolio.id))
    ).all()
    out = []
    for portfolio in portfolios:
        analytics = await PortfolioAnalytics.load(session, [portfolio.id])
        summary = analytics.summary()
        out.append(
            {
                "id": portfolio.id,
                "name": portfolio.name,
                "kind": portfolio.kind,
                "base_currency": portfolio.base_currency,
                "broker": portfolio.broker,
                "opened_at": portfolio.opened_at.isoformat() if portfolio.opened_at else None,
                "note": portfolio.note,
                "is_archived": portfolio.is_archived,
                "total_value": summary["total_value"],
                "total_pnl": summary["total_pnl"],
                "total_pnl_pct": summary["total_pnl_pct"],
                "day_change": summary["day_change"],
                "positions_count": summary["positions_count"],
            }
        )
    return out


# --------------------------------------------- корреляция произвольной пары

#: для «всего времени» по бумаге вне портфеля тянуть больше двух лет тяжело
PAIR_HISTORY_DAYS = 365 * 2


def _asset_meta(asset: Asset) -> dict:
    name = asset.short_name or asset.name
    return {
        "secid": asset.secid,
        "name": name,
        "asset_class": asset.asset_class,
        "asset_class_label": ASSET_CLASS_LABELS.get(asset.asset_class, asset.asset_class),
    }


def _info_meta(secid: str, info: dict) -> dict:
    asset_class = info.get("asset_class") or "SHARE"
    return {
        "secid": secid,
        "name": info.get("short_name") or info.get("name") or secid,
        "asset_class": asset_class,
        "asset_class_label": ASSET_CLASS_LABELS.get(asset_class, asset_class),
    }


async def _pair_series(
    session: AsyncSession,
    secid: str,
    start: date | None,
    end: date,
    iss: MoexISS,
) -> tuple[dict, list[tuple[date, float]], str | None]:
    """Цены одного актива: из локальной истории, при нехватке — из ISS."""
    asset = await session.scalar(select(Asset).where(Asset.secid == secid))
    points: list[tuple[date, float]] = []
    meta: dict = {}

    if asset is not None:
        meta = _asset_meta(asset)
        if asset.is_custom:
            return meta, [], f"по активу {secid} нет рыночной истории — это кастомный актив"
        stmt = select(PriceHistory.date, PriceHistory.close_norm).where(
            PriceHistory.asset_id == asset.id,
            PriceHistory.date <= end,
        )
        if start is not None:
            stmt = stmt.where(PriceHistory.date >= start)
        rows = await session.execute(stmt.order_by(PriceHistory.date))
        points = [
            (day, f(close))
            for day, close in rows
            if close is not None and f(close) > EPS
        ]
        if len(points) >= CORRELATION_MIN_RETURNS + 1:
            return meta, points, None

    if asset is None:
        info = await iss.security_info(secid)
        if not info:
            return {}, [], f"инструмент {secid} не найден на Мосбирже"
        meta = _info_meta(secid, info)
        engine = info.get("engine")
        market = info.get("market")
        board = info.get("board")
        asset_class = info.get("asset_class") or "SHARE"
        face = info.get("face_value") or info.get("initial_face_value")
    else:
        engine, market, board = asset.engine, asset.market, asset.board
        asset_class = asset.asset_class
        face = asset.face_value or asset.initial_face_value

    if not engine or not market:
        return meta, points, f"по активу {secid} неизвестен режим торгов"

    history_start = start or (end - timedelta(days=PAIR_HISTORY_DAYS))
    try:
        rows = await iss.history(engine, market, secid, history_start, end, board=board)
        if not rows and board:
            rows = await iss.history(engine, market, secid, history_start, end)
    except Exception as exc:  # noqa: BLE001 — ISS может быть недоступен
        log.warning("correlation_pair history(%s): %s", secid, exc)
        rows = []

    merged = dict(points)
    for row in rows:
        face_row = row.get("face_value") or face
        value = normalize_price(
            asset_class,
            row["close"],
            float(face_row) if face_row else None,
        )
        if value > EPS:
            merged[row["date"]] = value
    points = sorted(merged.items())

    if len(points) < CORRELATION_MIN_RETURNS + 1:
        return meta, points, f"по активу {secid} недостаточно истории цен"
    return meta, points, None


async def _pair_grid(
    session: AsyncSession,
    start: date,
    end: date,
    points: list[tuple[date, float]],
) -> list[date]:
    """Общая сетка торговых дней; при отсутствии IMOEX — даты самих бумаг."""
    rows = await session.execute(
        select(BenchmarkPrice.date)
        .join(Benchmark, Benchmark.id == BenchmarkPrice.benchmark_id)
        .where(
            Benchmark.code == "IMOEX",
            BenchmarkPrice.date >= start,
            BenchmarkPrice.date <= end,
        )
    )
    grid = sorted({row[0] for row in rows})
    if len(grid) < CORRELATION_MIN_RETURNS + 1:
        grid = sorted({day for day, _ in points if start <= day <= end})
    return grid


def _build_pair_result(
    left_meta: dict,
    right_meta: dict,
    left_points: list[tuple[date, float]],
    right_points: list[tuple[date, float]],
    grid: list[date],
    start: date | None,
    period: str,
) -> dict:
    """Считает доходности на общей сетке и собирает ответ для пары."""
    left_series = _returns_on_grid(left_points, grid)
    right_series = _returns_on_grid(right_points, grid)
    if start is not None:
        left_series = [(day, value) for day, value in left_series if day >= start]
        right_series = [(day, value) for day, value in right_series if day >= start]

    for meta, series in ((left_meta, left_series), (right_meta, right_series)):
        if len(series) < CORRELATION_MIN_RETURNS:
            return {
                "available": False,
                "period": period,
                "reason": (
                    f"по активу {meta.get('secid', '')} меньше "
                    f"{CORRELATION_MIN_RETURNS} торговых дней с доходностью"
                ),
            }

    left_map = dict(left_series)
    right_map = dict(right_series)
    common = sorted(set(left_map) & set(right_map))
    if len(common) < CORRELATION_MIN_RETURNS:
        return {
            "available": False,
            "period": period,
            "reason": (
                "у бумаг слишком мало общих торговых дней: "
                f"{len(common)} при минимуме {CORRELATION_MIN_RETURNS}"
            ),
        }

    stats = m.pair_statistics(
        [left_map[day] for day in common],
        [right_map[day] for day in common],
    )
    if stats is None:
        return {
            "available": False,
            "period": period,
            "reason": "у одной из бумаг нулевая волатильность за период",
        }

    def detail(meta: dict, series: list[tuple[date, float]]) -> dict:
        return {
            **meta,
            "observations": len(series),
            "volatility": m.volatility([value for _, value in series]),
            "first": series[0][0].isoformat() if series else None,
            "last": series[-1][0].isoformat() if series else None,
        }

    return {
        "available": True,
        "period": period,
        "start": common[0].isoformat(),
        "end": common[-1].isoformat(),
        "requested_start": start.isoformat() if start else None,
        "method": "дневные доходности чистых цен на общей сетке торговых дней",
        "left": detail(left_meta, left_series),
        "right": detail(right_meta, right_series),
        "correlation": stats["correlation"],
        "covariance": stats["covariance"],
        "covariance_annual": stats["covariance_annual"],
        "covariance_pct2": stats["covariance"] * 10_000,
        "covariance_annual_pct2": stats["covariance_annual"] * 10_000,
        "observations": stats["observations"],
        "min_observations": CORRELATION_MIN_RETURNS,
    }


async def correlation_pair(
    session: AsyncSession,
    left_secid: str,
    right_secid: str,
    period: str = "ALL",
) -> dict:
    """Корреляция двух любых активов, не обязательно входящих в портфель.

    Цены берутся из локальной истории, а для бумаг вне портфеля — напрямую
    из ISS. Считаются дневные доходности чистых цен на общей сетке торговых
    дней, поэтому результат совпадает с ячейкой матрицы для тех же бумаг.
    """
    left_code = (left_secid or "").strip().upper()
    right_code = (right_secid or "").strip().upper()
    if not left_code or not right_code:
        return {"available": False, "reason": "укажите два тикера"}
    if left_code == right_code:
        return {"available": False, "reason": "нужны две разные бумаги"}

    end = date.today()
    requested_start = period_start(period, None)
    fetch_start = (
        requested_start - timedelta(days=CORRELATION_LOOKBACK_DAYS)
        if requested_start
        else None
    )

    async with MoexISS() as iss:
        left_meta, left_points, left_error = await _pair_series(
            session, left_code, fetch_start, end, iss
        )
        right_meta, right_points, right_error = await _pair_series(
            session, right_code, fetch_start, end, iss
        )

    if left_error:
        return {"available": False, "period": period, "reason": left_error}
    if right_error:
        return {"available": False, "period": period, "reason": right_error}
    if not left_points or not right_points:
        return {
            "available": False,
            "period": period,
            "reason": "нет истории цен за выбранный период",
        }

    grid_start = fetch_start or min(day for day, _ in left_points + right_points)
    grid = await _pair_grid(session, grid_start, end, left_points + right_points)
    if not grid:
        return {
            "available": False,
            "period": period,
            "reason": "нет торговых дней за выбранный период",
        }

    return _build_pair_result(
        left_meta,
        right_meta,
        left_points,
        right_points,
        grid,
        requested_start,
        period,
    )
