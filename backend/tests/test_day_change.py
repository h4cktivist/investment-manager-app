"""Дневное изменение стоимости: база — чистая цена прошлого закрытия.

Проверяем источники расхождения с брокером:
* для акций предыдущее закрытие берётся из PREVPRICE (последняя сделка),
  а не из PREVLEGALCLOSEPRICE (цена закрытия);
* НКД в дневное изменение не входит — брокер считает его отдельно.
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services.analytics import PortfolioAnalytics
from app.services.portfolio import Position, PriceBook


def _book(
    series: list[tuple[date, float]],
    history: list[tuple[date, float, float]],
    live: float,
    asset_id: int = 1,
) -> PriceBook:
    return PriceBook(
        series={asset_id: series},
        live={asset_id: live},
        history={asset_id: history},
    )


def test_previous_clean_falls_back_to_history():
    book = _book(
        series=[
            (date(2026, 11, 19), 508.58 + 33.09),
            (date(2026, 11, 20), 506.24 + 33.07),
        ],
        history=[
            (date(2026, 11, 19), 508.58, 33.09),
            (date(2026, 11, 20), 506.24, 33.07),
        ],
        live=506.02 + 33.84,
    )

    # идёт торговля: предыдущее закрытие — последняя запись истории
    assert book.previous_clean(1, 540.10) == pytest.approx(506.24)
    # текущая цена равна последнему закрытию (выходной) — берём закрытие до него
    assert book.previous_clean(1, 506.24) == pytest.approx(508.58)
    assert book.previous_clean(1) == pytest.approx(506.24)


def _analytics(asset, position, quote, book) -> PortfolioAnalytics:
    ledger = SimpleNamespace(positions={asset.id: position})
    return PortfolioAnalytics(None, ledger, {asset.id: asset}, book, {asset.id: quote})


def _share() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        secid="SBER",
        short_name="Сбербанк",
        name="Сбербанк",
        asset_class="SHARE",
        currency="RUB",
        lot_size=1,
        face_value=None,
        initial_face_value=None,
        maturity_date=None,
        is_custom=False,
    )


def _bond() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        secid="SU26238RMFS4",
        short_name="ОФЗ 26238",
        name="ОФЗ 26238",
        asset_class="BOND",
        currency="RUB",
        lot_size=1,
        face_value=1000.0,
        initial_face_value=1000.0,
        maturity_date=date(2041, 5, 15),
        is_custom=False,
    )


def test_share_day_change_uses_previous_last_trade_not_legal_close():
    asset = _share()
    quote = SimpleNamespace(
        prev_close=287.74,
        accrued_interest=None,
        change_pct=-0.42,
        updated_at=datetime(2026, 11, 20, 18, 45),
        is_delayed=True,
    )
    book = _book(
        series=[(date(2026, 11, 20), 287.74)],
        history=[(date(2026, 11, 20), 287.74, 0.0)],
        live=286.53,
    )

    row = _analytics(asset, Position(asset_id=1, qty=10), quote, book).positions()[0]

    # брокер считает от PREVPRICE 287,74, а не от LEGALCLOSEPRICE 287,10
    assert row["day_change"] == pytest.approx((286.53 - 287.74) * 10)
    assert row["day_change_pct"] == pytest.approx(286.53 / 287.74 - 1.0)


def test_bond_day_change_is_clean_price_change_like_broker():
    """RU000A10BY45: 21 бумага, LAST 100,75, PREVPRICE 100,55 — у брокера +42 ₽.

    НКД сейчас 0 (перед купоном), а в прошлом закрытии было 14,42 ₽. Если
    подмешать НКД в базу, получится ложный минус почти на 303 ₽.
    """
    asset = _bond()
    quote = SimpleNamespace(
        prev_close=100.55,
        accrued_interest=0.0,
        change_pct=0.2,
        updated_at=datetime(2026, 11, 20, 18, 45),
        is_delayed=True,
    )
    book = _book(
        series=[
            (date(2026, 11, 19), 1005.5 + 13.87),
            (date(2026, 11, 20), 1007.5 + 14.42),
        ],
        history=[
            (date(2026, 11, 19), 1005.5, 13.87),
            (date(2026, 11, 20), 1007.5, 14.42),
        ],
        live=1007.5,
    )

    row = _analytics(asset, Position(asset_id=1, qty=21), quote, book).positions()[0]

    assert row["day_change"] == pytest.approx((1007.5 - 1005.5) * 21)
    assert row["day_change"] == pytest.approx(42.0)
    assert row["day_change_pct"] == pytest.approx(1007.5 / 1005.5 - 1.0)


def test_bond_day_change_ignores_daily_accrual():
    asset = _bond()
    quote = SimpleNamespace(
        prev_close=50.624,
        accrued_interest=33.84,
        change_pct=-0.04,
        updated_at=datetime(2026, 11, 20, 18, 45),
        is_delayed=True,
    )
    book = _book(
        series=[
            (date(2026, 11, 19), 508.58 + 33.09),
            (date(2026, 11, 20), 506.24 + 33.07),
        ],
        history=[
            (date(2026, 11, 19), 508.58, 33.09),
            (date(2026, 11, 20), 506.24, 33.07),
        ],
        live=506.02 + 33.84,
    )

    row = _analytics(asset, Position(asset_id=1, qty=10), quote, book).positions()[0]

    # НКД вырос на 0,77 ₽ за сессию, но в изменение за день он не входит
    assert row["day_change"] == pytest.approx((506.02 - 506.24) * 10)
    assert row["day_change_pct"] == pytest.approx(506.02 / 506.24 - 1.0)


def test_bond_without_accrued_in_quote_compares_clean_prices():
    """T-Invest не отдаёт НКД: нельзя вычитать его только из прошлой цены."""
    asset = _bond()
    quote = SimpleNamespace(
        prev_close=50.624,
        accrued_interest=None,
        change_pct=None,
        updated_at=datetime(2026, 11, 20, 18, 45),
        is_delayed=False,
    )
    book = _book(
        series=[
            (date(2026, 11, 19), 508.58 + 33.09),
            (date(2026, 11, 20), 506.24 + 33.07),
        ],
        history=[
            (date(2026, 11, 19), 508.58, 33.09),
            (date(2026, 11, 20), 506.24, 33.07),
        ],
        live=506.02,
    )

    row = _analytics(asset, Position(asset_id=1, qty=10), quote, book).positions()[0]

    assert row["day_change"] == pytest.approx((506.02 - 506.24) * 10)
    assert row["day_change_pct"] == pytest.approx(506.02 / 506.24 - 1.0)
