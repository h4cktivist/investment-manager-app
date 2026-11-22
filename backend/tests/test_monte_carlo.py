"""Сборка ответа Монте-Карло без БД.

Подменяем ряд переоценки: проверяем интеграцию сервиса — горизонты,
даты будущих торговых дней и структуру точек для графика.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from app.services.analytics import PortfolioAnalytics
from app.services.portfolio import ValuationPoint


class _Analytics(PortfolioAnalytics):
    def __init__(self, points: list[ValuationPoint]):
        self._points = points
        self.ledger = SimpleNamespace(start_date=points[0].day)

    async def valuation_with_baseline(self, start, end):
        return None, self._points


def _points(trading_days: int = 120) -> list[ValuationPoint]:
    start = date.today() - timedelta(days=trading_days + 40)
    total = 100_000.0
    points = [ValuationPoint(start, total, 0, total, 0, total, 0)]
    for offset in range(1, trading_days + 1):
        # чередуем рост и падение, чтобы у ряда была волатильность
        total *= 1.01 if offset % 2 else 0.995
        day = start + timedelta(days=offset)
        points.append(ValuationPoint(day, total, 0, total, 0, total, 0))
    return points


def test_monte_carlo_service_assembles_dates_and_horizon():
    analytics = _Analytics(_points())

    data = asyncio.run(analytics.monte_carlo(period="ALL", horizon="3M", simulations=200))

    assert data["available"] is True
    assert data["horizon"] == "3M"
    assert data["horizon_days"] == 63
    assert data["simulations"] == 200
    assert data["points"][0]["date"] == date.today().isoformat()
    assert len(data["points"]) == 64
    assert data["points"][-1]["date"] > data["points"][0]["date"]
    assert data["points"][0]["p50"] == data["initial_value"]
    assert data["final"]["p5"] <= data["final"]["p50"] <= data["final"]["p95"]


def test_monte_carlo_service_reports_insufficient_history():
    analytics = _Analytics(_points(trading_days=10))

    data = asyncio.run(analytics.monte_carlo(period="ALL", horizon="1Y"))

    assert data["available"] is False
    assert "торговых дней" in data["reason"]
