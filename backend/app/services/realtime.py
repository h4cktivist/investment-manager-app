"""Онлайн-котировки: провайдеры, фоновый опрос и рассылка по WebSocket.

Про «риалтайм» честно: анонимный MOEX ISS отдаёт торговые данные с задержкой
около 15 минут — это потолок бесплатного доступа без брокерского API.
Если в .env положить TINVEST_TOKEN, включается провайдер T-Invest API,
который отдаёт цены без задержки (токен бесплатный при наличии счёта).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.db import SessionLocal
from app.enums import AssetClass
from app.models import Asset, Quote
from app.services.moex import MoexISS, normalize_price

log = logging.getLogger(__name__)


@dataclass(slots=True)
class QuoteTick:
    secid: str
    price: float
    price_norm: float
    prev_close: float | None = None
    change_pct: float | None = None
    accrued_interest: float | None = None
    bid: float | None = None
    ask: float | None = None
    source: str = "moex"
    is_delayed: bool = True
    updated_at: str = ""

    def to_json(self) -> dict:
        data = asdict(self)
        data["updated_at"] = self.updated_at or datetime.now(timezone.utc).isoformat()
        return data


class QuoteProvider(Protocol):
    name: str
    is_delayed: bool

    async def fetch(self, assets: Iterable[Asset]) -> dict[str, QuoteTick]: ...


# --------------------------------------------------------------------- MOEX


class MoexQuoteProvider:
    name = "moex"
    is_delayed = True

    async def fetch(self, assets: Iterable[Asset]) -> dict[str, QuoteTick]:
        groups: dict[tuple[str, str, str | None], list[Asset]] = {}
        for asset in assets:
            if asset.is_custom or not asset.engine or not asset.market:
                continue
            groups.setdefault((asset.engine, asset.market, asset.board), []).append(asset)

        out: dict[str, QuoteTick] = {}
        async with MoexISS() as iss:
            for (engine, market, board), items in groups.items():
                secids = [a.secid for a in items]
                try:
                    data = await iss.market_data(engine, market, board, secids)
                except Exception as exc:  # noqa: BLE001
                    log.warning("market_data(%s/%s/%s): %s", engine, market, board, exc)
                    continue
                by_secid = {a.secid: a for a in items}
                for secid, row in data.items():
                    asset = by_secid.get(secid)
                    if asset is None:
                        continue
                    face = row.get("face_value") or float(
                        asset.face_value or asset.initial_face_value or 0
                    )
                    out[secid] = QuoteTick(
                        secid=secid,
                        price=row["price"],
                        price_norm=normalize_price(asset.asset_class, row["price"], face or None),
                        prev_close=row.get("prev_close"),
                        change_pct=row.get("change_pct"),
                        accrued_interest=row.get("accrued_interest"),
                        bid=row.get("bid"),
                        ask=row.get("ask"),
                        source=self.name,
                        is_delayed=True,
                    )
        return out


# ----------------------------------------------------------------- T-Invest

TINVEST_BASE = "https://invest-public-api.tinkoff.ru/rest"


def _quotation(value: dict | None) -> float | None:
    if not value:
        return None
    units = float(value.get("units") or 0)
    nano = float(value.get("nano") or 0)
    return units + nano / 1e9


class TinvestQuoteProvider:
    """Реальный realtime через T-Invest API (нужен бесплатный токен)."""

    name = "tinvest"
    is_delayed = False

    def __init__(self, token: str) -> None:
        self._token = token
        self._uid_cache: dict[str, dict] = {}

    async def _post(self, client: httpx.AsyncClient, method: str, payload: dict) -> dict:
        resp = await client.post(
            f"{TINVEST_BASE}/tinkoff.public.invest.api.contract.v1.{method}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _resolve(self, client: httpx.AsyncClient, asset: Asset) -> dict | None:
        key = asset.secid
        if key in self._uid_cache:
            return self._uid_cache[key]
        query = asset.isin or asset.secid
        try:
            data = await self._post(
                client, "InstrumentsService/FindInstrument", {"query": query, "apiTradeAvailableFlag": False}
            )
        except httpx.HTTPError as exc:
            log.warning("tinvest FindInstrument(%s): %s", query, exc)
            return None
        instruments = data.get("instruments", [])
        if not instruments:
            return None
        # предпочитаем инструмент, торгуемый на Мосбирже
        item = next(
            (i for i in instruments if (i.get("classCode") or "").startswith("TQ")),
            instruments[0],
        )
        self._uid_cache[key] = item
        return item

    async def fetch(self, assets: Iterable[Asset]) -> dict[str, QuoteTick]:
        tradable = [a for a in assets if not a.is_custom]
        if not tradable:
            return {}
        out: dict[str, QuoteTick] = {}
        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            uid_to_asset: dict[str, Asset] = {}
            for asset in tradable:
                item = await self._resolve(client, asset)
                if item and item.get("uid"):
                    uid_to_asset[item["uid"]] = asset
            if not uid_to_asset:
                return {}
            try:
                data = await self._post(
                    client,
                    "MarketDataService/GetLastPrices",
                    {"instrumentId": list(uid_to_asset)},
                )
            except httpx.HTTPError as exc:
                log.warning("tinvest GetLastPrices: %s", exc)
                return {}
            for row in data.get("lastPrices", []):
                asset = uid_to_asset.get(row.get("instrumentUid", ""))
                price = _quotation(row.get("price"))
                if asset is None or price is None:
                    continue
                face = float(asset.face_value or asset.initial_face_value or 0) or None
                out[asset.secid] = QuoteTick(
                    secid=asset.secid,
                    price=price,
                    price_norm=normalize_price(asset.asset_class, price, face),
                    source=self.name,
                    is_delayed=False,
                    updated_at=row.get("time", ""),
                )
        return out


def build_provider() -> QuoteProvider:
    if settings.quote_provider == "tinvest" and settings.tinvest_token:
        log.info("quote provider: T-Invest (realtime)")
        return TinvestQuoteProvider(settings.tinvest_token)
    if settings.quote_provider == "tinvest":
        log.warning("QUOTE_PROVIDER=tinvest, но TINVEST_TOKEN пуст — откат на MOEX ISS")
    log.info("quote provider: MOEX ISS (задержка ~15 минут)")
    return MoexQuoteProvider()


# ------------------------------------------------------------------ WS-хаб


class QuoteHub:
    """Держит подписчиков WebSocket и рассылает им обновления цен."""

    def __init__(self) -> None:
        self._clients: set = set()
        self._lock = asyncio.Lock()
        self.last_ticks: dict[str, dict] = {}

    async def register(self, websocket) -> None:
        async with self._lock:
            self._clients.add(websocket)
        if self.last_ticks:
            with contextlib.suppress(Exception):
                await websocket.send_text(
                    json.dumps({"type": "snapshot", "quotes": list(self.last_ticks.values())})
                )

    async def unregister(self, websocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, ticks: list[QuoteTick]) -> None:
        if not ticks:
            return
        payload = [t.to_json() for t in ticks]
        for item in payload:
            self.last_ticks[item["secid"]] = item
        message = json.dumps({"type": "quotes", "quotes": payload})
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001 — клиент отвалился
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


hub = QuoteHub()


# ------------------------------------------------------------------- опрос


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


async def refresh_quotes_once(provider: QuoteProvider | None = None) -> int:
    """Один проход опроса котировок: ISS -> БД -> WebSocket."""
    provider = provider or build_provider()
    async with SessionLocal() as session:
        assets = (
            await session.scalars(
                select(Asset).where(Asset.is_active.is_(True), Asset.is_custom.is_(False))
            )
        ).all()
        if not assets:
            return 0

        ticks = await provider.fetch(assets)
        if not ticks:
            return 0

        by_secid = {a.secid: a for a in assets}
        rows = []
        for secid, tick in ticks.items():
            asset = by_secid.get(secid)
            if asset is None:
                continue
            rows.append(
                {
                    "asset_id": asset.id,
                    "price": _dec(tick.price),
                    "price_norm": _dec(tick.price_norm),
                    "prev_close": _dec(tick.prev_close),
                    "change_pct": _dec(tick.change_pct),
                    "accrued_interest": _dec(tick.accrued_interest),
                    "bid": _dec(tick.bid),
                    "ask": _dec(tick.ask),
                    "source": tick.source,
                    "is_delayed": tick.is_delayed,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        if rows:
            stmt = pg_insert(Quote).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Quote.asset_id],
                set_={
                    c: stmt.excluded[c]
                    for c in (
                        "price",
                        "price_norm",
                        "prev_close",
                        "change_pct",
                        "accrued_interest",
                        "bid",
                        "ask",
                        "source",
                        "is_delayed",
                        "updated_at",
                    )
                },
            )
            await session.execute(stmt)
            await session.commit()

    await hub.broadcast(list(ticks.values()))
    return len(ticks)


async def quote_poller() -> None:
    provider = build_provider()
    while True:
        try:
            count = await refresh_quotes_once(provider)
            log.debug("quotes refreshed: %s", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("quote poller error: %s", exc)
        await asyncio.sleep(max(settings.quote_poll_seconds, 5))
