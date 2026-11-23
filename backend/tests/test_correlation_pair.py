"""Корреляция двух произвольных активов."""
from __future__ import annotations

import asyncio
import math
from datetime import date, timedelta

import pytest

from app.services.analytics import _build_pair_result, correlation_pair

LEFT = {
    "secid": "SBER",
    "name": "Сбербанк",
    "asset_class": "SHARE",
    "asset_class_label": "Акции",
}
RIGHT = {
    "secid": "GAZP",
    "name": "Газпром",
    "asset_class": "SHARE",
    "asset_class_label": "Акции",
}


def _days(count: int) -> list[date]:
    day = date(2026, 1, 5)
    out: list[date] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _series(
    days: list[date], start: float, rate: float, noise: float = 0.0
) -> list[tuple[date, float]]:
    # общая колебательная компонента: без неё доходности постоянны и
    # выборочное стандартное отклонение получается нулевым
    value = start
    out: list[tuple[date, float]] = []
    for i, day in enumerate(days):
        value *= 1 + rate + noise * math.sin(i * 1.7)
        out.append((day, value))
    return out


def test_pair_result_for_moving_together():
    days = _days(30)
    result = _build_pair_result(
        LEFT,
        RIGHT,
        _series(days, 100, 0.01, 0.005),
        _series(days, 200, 0.02, 0.005),
        days,
        None,
        "ALL",
    )

    assert result["available"] is True
    assert result["correlation"] > 0.9
    assert result["observations"] == 29
    assert result["covariance"] > 0
    assert result["covariance_annual"] == pytest.approx(result["covariance"] * 252)
    assert result["covariance_pct2"] == pytest.approx(result["covariance"] * 10_000)
    assert result["left"]["observations"] == 29
    assert result["right"]["observations"] == 29
    assert result["left"]["volatility"] > 0


def test_pair_result_for_moving_apart():
    days = _days(30)
    result = _build_pair_result(
        LEFT,
        RIGHT,
        _series(days, 100, 0.01, 0.005),
        _series(days, 200, -0.01, -0.005),
        days,
        None,
        "ALL",
    )

    assert result["available"] is True
    assert result["correlation"] < -0.9


def test_pair_result_respects_period_start():
    days = _days(30)
    start = days[10]
    result = _build_pair_result(
        LEFT,
        RIGHT,
        _series(days, 100, 0.01, 0.005),
        _series(days, 200, 0.02, 0.005),
        days,
        start,
        "1M",
    )

    assert result["available"] is True
    assert result["start"] == start.isoformat()
    assert result["end"] == days[-1].isoformat()
    assert result["observations"] == 20


def test_pair_result_needs_enough_observations():
    days = _days(5)
    result = _build_pair_result(
        LEFT,
        RIGHT,
        _series(days, 100, 0.01, 0.005),
        _series(days, 200, 0.02),
        days,
        None,
        "1M",
    )

    assert result["available"] is False
    assert "10" in result["reason"]


def test_pair_result_needs_variation():
    days = _days(30)
    flat = [(day, 100.0) for day in days]
    result = _build_pair_result(LEFT, RIGHT, flat, _series(days, 200, 0.01), days, None, "ALL")

    assert result["available"] is False
    assert "волатильность" in result["reason"]


def test_same_ticker_is_rejected():
    result = asyncio.run(correlation_pair(object(), "SBER", "sber"))  # type: ignore[arg-type]

    assert result["available"] is False
    assert "разные" in result["reason"]
