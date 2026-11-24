"""TLS для внешних API: доверяем сертификату российского УЦ, не отключая проверку."""
from __future__ import annotations

import asyncio
import ssl
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.services import realtime
from app.services.moex import MoexError, MoexISS
from app.services.tls import CERTS_DIR, external_ssl_context


@pytest.fixture(autouse=True)
def _clear_context_cache():
    external_ssl_context.cache_clear()
    yield
    external_ssl_context.cache_clear()


def _subjects(context: ssl.SSLContext) -> str:
    return " | ".join(str(cert.get("subject")) for cert in context.get_ca_certs())


def test_bundled_ca_is_committed():
    assert (CERTS_DIR / "russian_trusted_root_ca.pem").is_file()


def test_default_context_trusts_russian_root():
    context = external_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert "Russian Trusted Root CA" in _subjects(context)
    # обычные мировые корни из certifi никуда не делись
    assert "ISRG Root X1" in _subjects(context)


def test_custom_bundle_replaces_default(monkeypatch, tmp_path):
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text(
        (CERTS_DIR / "russian_trusted_root_ca.pem").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "ssl_ca_bundle", str(bundle))

    context = external_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert "Russian Trusted Root CA" in _subjects(context)
    assert "ISRG Root X1" not in _subjects(context)


def test_missing_custom_bundle_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ssl_ca_bundle", str(tmp_path / "nope.pem"))

    with pytest.raises(FileNotFoundError):
        external_ssl_context()


def test_moex_client_uses_shared_context(monkeypatch):
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            pass

    monkeypatch.setattr("app.services.moex.httpx.AsyncClient", _FakeClient)

    async def run():
        async with MoexISS():
            pass

    asyncio.run(run())

    assert captured["verify"] is external_ssl_context()


def test_get_does_not_retry_certificate_errors():
    class _FailingClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            raise httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "self-signed certificate in certificate chain"
            )

    iss = MoexISS()
    client = _FailingClient()
    iss._client = client

    with pytest.raises(MoexError) as info:
        asyncio.run(iss._get("/engines/stock/markets/bonds/boards/TQRD/securities.json"))

    # повторы бессмысленны: тот же сертификат не станет доверенным
    assert client.calls == 1
    assert "CERTIFICATE_VERIFY_FAILED" in str(info.value)


def test_quote_provider_continues_after_failed_board(monkeypatch):
    class _FakeISS:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

        async def market_data(self, _engine, _market, board, _secids):
            if board == "TQRD":
                raise MoexError("ISS TLS failure")
            return {
                "SBER": {
                    "price": 100.0,
                    "prev_close": 99.0,
                    "change_pct": 1.01,
                    "accrued_interest": None,
                    "bid": None,
                    "ask": None,
                    "face_value": None,
                }
            }

    monkeypatch.setattr(realtime, "MoexISS", _FakeISS)

    def asset(**overrides):
        fields = {
            "is_custom": False,
            "engine": "stock",
            "market": "bonds",
            "board": "TQRD",
            "secid": "TQRD",
            "asset_class": "BOND",
            "face_value": 1000.0,
            "initial_face_value": 1000.0,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    bond = asset()
    share = asset(market="shares", board="TQBR", secid="SBER", asset_class="SHARE")

    quotes = asyncio.run(realtime.MoexQuoteProvider().fetch([bond, share]))

    assert "SBER" in quotes
    assert quotes["SBER"].price == pytest.approx(100.0)
