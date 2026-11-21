"""Сборка матрицы корреляций без БД.

Подменяем позиции, торговый календарь и выборку цен: проверяем именно
ассемблирование ответа, а не SQL.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.analytics import PortfolioAnalytics


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _Result(self._rows)


class _Analytics(PortfolioAnalytics):
    def __init__(self, positions, rows, days, start):
        self._positions = positions
        self._rows = rows
        self._days = days
        self.session = _Session(rows)
        self.ledger = SimpleNamespace(start_date=start)

    def positions(self):
        return self._positions

    async def trading_days(self, start, end):
        return {day for day in self._days if start <= day <= end}


def _position(asset_id: int, secid: str, value: float, weight: float) -> dict:
    return {
        "asset_id": asset_id,
        "secid": secid,
        "name": f"{secid} name",
        "asset_class": "SHARE",
        "asset_class_label": "Акции",
        "is_open": True,
        "is_custom": False,
        "has_market_price": True,
        "market_value": value,
        "weight": weight,
    }


def _market(days: list[date]) -> tuple[list[dict], list[tuple[int, date, float]]]:
    """Три актива: A и B движутся вместе, C — в противофазе."""
    rows: list[tuple[int, date, float]] = []
    price_a, price_b, price_c = 100.0, 200.0, 50.0
    for index, day in enumerate(days):
        if index:
            move = 0.01 if index % 2 else -0.01
            price_a *= 1 + move
            price_b *= 1 + 2 * move
            price_c *= 1 - move
        rows.append((1, day, price_a))
        rows.append((2, day, price_b))
        rows.append((3, day, price_c))

    positions = [
        _position(1, "AAA", price_a, 0.5),
        _position(2, "BBB", price_b, 0.3),
        _position(3, "CCC", price_c, 0.2),
    ]
    return positions, rows


def _days(count: int = 41) -> list[date]:
    today = date.today()
    return [today - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def test_correlation_matrix_assembles_correlations_and_covariances():
    days = _days()
    positions, rows = _market(days)
    analytics = _Analytics(positions, rows, days, days[0])

    data = asyncio.run(analytics.correlation_matrix(period="1M", max_assets=15))

    assert data["available"] is True
    assert {asset["secid"] for asset in data["assets"]} == {"AAA", "BBB", "CCC"}
    index = {asset["secid"]: i for i, asset in enumerate(data["assets"])}
    i_a, i_b, i_c = index["AAA"], index["BBB"], index["CCC"]

    assert data["correlation"][i_a][i_a] == 1.0
    assert data["correlation"][i_a][i_b] == pytest.approx(1.0)
    assert data["correlation"][i_a][i_c] == pytest.approx(-1.0)
    assert data["truncated"] is False
    assert data["excluded_no_history"] == 0

    pair = next(
        item
        for item in data["pairs"]
        if {item["i"], item["j"]} == {i_a, i_b}
    )
    assert pair["observations"] >= data["min_observations"]
    assert pair["covariance_pct2"] == pytest.approx(pair["covariance"] * 10_000)
    assert pair["covariance_annual_pct2"] == pytest.approx(
        pair["covariance_annual"] * 10_000
    )
    assert pair["covariance_annual"] == pytest.approx(pair["covariance"] * 252)


def test_correlation_matrix_limits_assets_and_reports_truncation():
    days = _days()
    positions, rows = _market(days)
    analytics = _Analytics(positions, rows, days, days[0])

    data = asyncio.run(analytics.correlation_matrix(period="1M", max_assets=2))

    assert data["available"] is True
    assert len(data["assets"]) == 2
    assert data["total_assets"] == 3
    assert data["truncated"] is True


def test_correlation_matrix_requires_two_assets():
    days = _days()
    positions, rows = _market(days)
    analytics = _Analytics(positions[:1], rows, days, days[0])

    data = asyncio.run(analytics.correlation_matrix(period="1M"))

    assert data["available"] is False
    assert "две" in data["reason"]
