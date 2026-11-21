"""Цена и НКД на дату для автозаполнения сделки."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.market_data import quote_for_date


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, _query):
        return _Result(self._row)


class _Iss:
    async def market_data(self, *_args, **_kwargs):
        return {"XXX": {"price": 99.5, "face_value": 1000, "accrued_interest": 7.25}}


def _asset(**overrides):
    fields = {
        "id": 1,
        "secid": "XXX",
        "asset_class": "BOND",
        "engine": "stock",
        "market": "bonds",
        "board": "TQCB",
        "is_custom": False,
        "face_value": 1000.0,
        "initial_face_value": 1000.0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_quote_for_date_takes_price_and_nkd_from_history():
    row = (date(2025, 1, 10), Decimal("990.5"), Decimal("12.3"))
    quote = asyncio.run(quote_for_date(_Session(row), _asset(), date(2025, 1, 10)))

    assert quote["price"] == 990.5
    assert quote["accrued_interest"] == 12.3
    assert quote["as_of"] == date(2025, 1, 10)
    assert quote["source"] == "history"


def test_quote_for_date_uses_live_quote_for_today():
    quote = asyncio.run(
        quote_for_date(_Session(None), _asset(), date.today(), iss=_Iss())
    )

    # 99,5 % от номинала 1000 → 995 рублей
    assert quote["price"] == 995.0
    assert quote["accrued_interest"] == 7.25
    assert quote["source"] == "iss_quote"


def test_quote_for_date_returns_empty_without_data():
    quote = asyncio.run(quote_for_date(_Session(None), _asset(), date(2025, 1, 10)))

    assert quote["price"] is None
    assert quote["accrued_interest"] is None
    assert quote["source"] is None


def test_quote_for_date_skips_custom_assets():
    quote = asyncio.run(
        quote_for_date(_Session(None), _asset(is_custom=True), date(2025, 1, 10), iss=_Iss())
    )

    assert quote["price"] is None
