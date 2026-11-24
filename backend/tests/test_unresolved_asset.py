"""Починка «заглушек», созданных при недоступном ISS (например, из-за TLS)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.market_data import (
    apply_iss_reference,
    get_or_create_asset,
    is_unresolved,
    refresh_asset_reference,
)

ISS_INFO = {
    "secid": "RU000A10G2M8",
    "name": "ЗАСЛОН 002Р-01",
    "short_name": "ЗАСЛОН 2Р1",
    "isin": "RU000A10G2M8",
    "asset_class": "BOND",
    "currency": "RUB",
    "engine": "stock",
    "market": "bonds",
    "board": "TQCB",
    "lot_size": 1,
    "face_value": 1000.0,
    "initial_face_value": 1000.0,
    "coupon_rate": 17.5,
    "coupon_period": 30,
    "maturity_date": None,
    "offer_date": None,
    "is_traded": True,
    "type": "exchange_bond",
    "group": "stock_bonds",
}


def _stub(**overrides):
    fields = {
        "id": 44,
        "secid": "RU000A10G2M8",
        "name": "RU000A10G2M8",
        "short_name": "RU000A10G2M8",
        "asset_class": "CUSTOM",
        "currency": "RUB",
        "engine": None,
        "market": None,
        "board": None,
        "isin": None,
        "lot_size": 1,
        "face_value": None,
        "initial_face_value": None,
        "coupon_rate": None,
        "coupon_period": None,
        "maturity_date": None,
        "offer_date": None,
        "is_active": True,
        "is_custom": True,
        "meta": {"unresolved": True},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _Iss:
    def __init__(self, info=ISS_INFO, error: Exception | None = None):
        self.info = info
        self.error = error

    async def security_info(self, _secid):
        if self.error is not None:
            raise self.error
        return self.info


class _Session:
    def __init__(self, existing=None):
        self.existing = existing
        self.added: list = []
        self.flushes = 0

    async def scalar(self, _stmt):
        return self.existing

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


def test_is_unresolved_only_for_stubs():
    assert is_unresolved(_stub()) is True
    assert is_unresolved(_stub(meta={})) is False
    assert is_unresolved(_stub(is_custom=False, meta={"unresolved": True})) is False


def test_apply_iss_reference_turns_stub_into_bond():
    asset = _stub()

    apply_iss_reference(asset, ISS_INFO)

    assert asset.asset_class == "BOND"
    assert asset.engine == "stock"
    assert asset.market == "bonds"
    assert asset.board == "TQCB"
    assert asset.is_custom is False
    assert asset.name == "ЗАСЛОН 002Р-01"
    assert asset.face_value == pytest.approx(1000.0)
    assert "unresolved" not in asset.meta


def test_get_or_create_asset_repairs_existing_stub():
    asset = _stub()
    session = _Session(existing=asset)

    result = asyncio.run(get_or_create_asset(session, "ru000a10g2m8", _Iss()))

    assert result is asset
    assert asset.asset_class == "BOND"
    assert asset.is_custom is False
    assert session.flushes == 1


def test_get_or_create_asset_keeps_stub_when_iss_is_down():
    session = _Session()

    result = asyncio.run(
        get_or_create_asset(session, "RU000A10G2M8", _Iss(error=RuntimeError("ISS down")))
    )

    assert result.is_custom is True
    assert result.meta == {"unresolved": True}
    assert session.added == [result]


def test_get_or_create_asset_fills_new_asset_from_iss():
    session = _Session()

    result = asyncio.run(get_or_create_asset(session, "RU000A10G2M8", _Iss()))

    assert result.is_custom is False
    assert result.asset_class == "BOND"
    assert result.currency == "RUB"
    assert result.lot_size == 1
    assert session.added == [result]


def test_refresh_asset_reference_repairs_stub():
    asset = _stub()
    session = _Session(existing=asset)

    asyncio.run(refresh_asset_reference(session, asset, _Iss()))

    assert asset.asset_class == "BOND"
    assert asset.is_custom is False
