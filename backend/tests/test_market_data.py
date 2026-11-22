"""Цена и НКД на дату для автозаполнения сделки."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.market_data import quote_for_date
from app.services.moex import MoexISS


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


def test_market_data_uses_last_trade_price_as_previous_close():
    payload = {
        "securities": {
            "columns": [
                "SECID",
                "PREVPRICE",
                "PREVLEGALCLOSEPRICE",
                "ACCRUEDINT",
                "FACEVALUE",
            ],
            "data": [
                ["SBER", 287.74, 287.10, None, 3],
                ["SU26238RMFS4", 50.624, 50.551, 33.84, 1000],
            ],
        },
        "marketdata": {
            "columns": ["SECID", "LAST", "LASTTOPREVPRICE", "LASTCHANGEPRCNT"],
            "data": [
                ["SBER", 286.53, -0.42, 0],
                ["SU26238RMFS4", 50.602, -0.04, 0],
            ],
        },
    }
    iss = MoexISS()

    async def fake_get(_path, _params=None):
        return payload

    iss._get = fake_get
    data = asyncio.run(
        iss.market_data("stock", "shares", "TQBR", ["SBER", "SU26238RMFS4"])
    )

    # PREVPRICE — последняя сделка прошлого дня; PREVLEGALCLOSEPRICE — закрытие
    assert data["SBER"]["prev_close"] == pytest.approx(287.74)
    assert data["SU26238RMFS4"]["prev_close"] == pytest.approx(50.624)
    # LASTCHANGEPRCNT после закрытия равен нулю, поэтому берём LASTTOPREVPRICE
    assert data["SBER"]["change_pct"] == pytest.approx(-0.42)
    assert data["SU26238RMFS4"]["change_pct"] == pytest.approx(-0.04)
    assert data["SU26238RMFS4"]["accrued_interest"] == pytest.approx(33.84)
