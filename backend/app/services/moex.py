"""Клиент MOEX ISS (https://iss.moex.com/iss/reference/).

Публичный, бесплатный, без ключа. Торговые данные для анонимных запросов
отдаются с задержкой ~15 минут — это и есть потолок «бесплатного риалтайма»
на Мосбирже без брокерского API.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = settings.moex_iss_base

#: TYPE из ISS -> наш AssetClass
_TYPE_TO_CLASS = {
    "common_share": "SHARE",
    "preferred_share": "SHARE",
    "depositary_receipt": "SHARE",
    "ofz_bond": "BOND",
    "corporate_bond": "BOND",
    "exchange_bond": "BOND",
    "subfederal_bond": "BOND",
    "municipal_bond": "BOND",
    "cb_bond": "BOND",
    "ifi_bond": "BOND",
    "euro_bond": "BOND",
    "public_ppif": "ETF",
    "private_ppif": "ETF",
    "interval_ppif": "ETF",
    "etf_ppif": "ETF",
    "currency": "CURRENCY",
}

_GROUP_TO_CLASS = {
    "stock_shares": "SHARE",
    "stock_bonds": "BOND",
    "stock_etf": "ETF",
    "stock_ppif": "ETF",
    "currency_selt": "CURRENCY",
}


class MoexError(RuntimeError):
    pass


def _rows(payload: dict[str, Any], block: str) -> list[dict[str, Any]]:
    """Разворачивает ISS-блок {columns: [...], data: [[...]]} в список словарей."""
    node = payload.get(block)
    if not node:
        return []
    columns = node.get("columns") or []
    return [dict(zip(columns, row)) for row in node.get("data") or []]


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if not value or value in ("0000-00-00",):
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class MoexISS:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(4)

    async def __aenter__(self) -> MoexISS:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.http_timeout,
                headers={"User-Agent": "investment-manager/1.0"},
                # ISS обрывает соединения при длинных сериях запросов —
                # держим небольшой пул и переиспользуем его
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._client is None:
            raise MoexError("MoexISS используется вне контекстного менеджера")
        query: dict[str, Any] = {"iss.meta": "off"}
        query.update(params or {})
        url = f"{BASE}{path}"
        attempts = 5
        async with self._semaphore:
            for attempt in range(attempts):
                try:
                    resp = await self._client.get(url, params=query)
                    if resp.status_code in (429, 502, 503, 504):
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp
                        )
                    resp.raise_for_status()
                    return resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == attempts - 1:
                        raise MoexError(f"ISS {path}: {type(exc).__name__} {exc}") from exc
                    await asyncio.sleep(min(0.8 * 2**attempt, 8.0))
        raise MoexError("unreachable")

    # ------------------------------------------------------------------ поиск

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self._get("/securities.json", {"q": query, "limit": limit})
        out = []
        for row in _rows(payload, "securities"):
            if not row.get("is_traded"):
                continue
            group = row.get("group") or ""
            out.append(
                {
                    "secid": row.get("secid"),
                    "name": row.get("name") or row.get("shortname"),
                    "short_name": row.get("shortname"),
                    "isin": row.get("isin"),
                    "asset_class": _GROUP_TO_CLASS.get(group, "SHARE"),
                    "engine": row.get("engine") or "stock",
                    "board": row.get("primary_boardid"),
                    "type": row.get("type"),
                    "group": group,
                }
            )
        return out

    # -------------------------------------------------------------- справочник

    async def security_info(self, secid: str) -> dict[str, Any] | None:
        """Полная карточка инструмента: описание + основная доска."""
        payload = await self._get(f"/securities/{secid}.json")
        desc = {r["name"]: r.get("value") for r in _rows(payload, "description")}
        boards = _rows(payload, "boards")
        if not desc and not boards:
            return None

        primary = next(
            (b for b in boards if b.get("is_primary") == 1 and b.get("is_traded") == 1),
            None,
        ) or next((b for b in boards if b.get("is_traded") == 1), None) or (
            boards[0] if boards else {}
        )

        sec_type = (desc.get("TYPE") or "").lower()
        group = (desc.get("GROUP") or primary.get("market") or "").lower()
        asset_class = _TYPE_TO_CLASS.get(sec_type) or _GROUP_TO_CLASS.get(group)
        if asset_class is None:
            asset_class = "BOND" if primary.get("market") == "bonds" else "SHARE"

        face = _to_float(desc.get("FACEVALUE"))
        initial_face = _to_float(desc.get("INITIALFACEVALUE")) or face

        # COUPONFREQUENCY в описании — число выплат в год; храним период в днях
        frequency = _to_float(desc.get("COUPONFREQUENCY"))
        coupon_period_days = int(round(365 / frequency)) if frequency and frequency > 0 else None

        return {
            "secid": secid,
            "name": desc.get("NAME") or desc.get("SHORTNAME") or secid,
            "short_name": desc.get("SHORTNAME"),
            "isin": desc.get("ISIN"),
            "asset_class": asset_class,
            "currency": _normalize_currency(desc.get("FACEUNIT") or desc.get("CURRENCYID")),
            "engine": primary.get("engine"),
            "market": primary.get("market"),
            "board": primary.get("boardid"),
            "lot_size": int(_to_float(desc.get("LOTSIZE")) or 1),
            "face_value": face,
            "initial_face_value": initial_face,
            "coupon_rate": _to_float(desc.get("COUPONPERCENT")),
            "coupon_period": coupon_period_days,
            "maturity_date": _to_date(desc.get("MATDATE")),
            "offer_date": _to_date(desc.get("BUYBACKDATE")),
            "is_traded": bool(primary.get("is_traded")),
            "type": sec_type,
            "group": group,
        }

    # ------------------------------------------------------------- котировки

    async def market_data(
        self, engine: str, market: str, board: str | None, secids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Текущие котировки пачкой. Возвращает {secid: {...}}."""
        if not secids:
            return {}
        path = (
            f"/engines/{engine}/markets/{market}/boards/{board}/securities.json"
            if board
            else f"/engines/{engine}/markets/{market}/securities.json"
        )
        payload = await self._get(
            path,
            {
                "securities": ",".join(secids),
                "iss.only": "securities,marketdata",
                "securities.columns": (
                    "SECID,SHORTNAME,PREVPRICE,PREVLEGALCLOSEPRICE,PREVADMITTEDQUOTE,"
                    "LOTSIZE,FACEVALUE,ACCRUEDINT,COUPONVALUE,COUPONPERCENT,COUPONPERIOD,"
                    "NEXTCOUPON,MATDATE,CURRENCYID,FACEUNIT"
                ),
                "marketdata.columns": (
                    "SECID,LAST,LCLOSEPRICE,LCURRENTPRICE,MARKETPRICE,WAPRICE,BID,OFFER,"
                    "LASTTOPREVPRICE,LASTCHANGEPRCNT,UPDATETIME,VALTODAY,YIELD,DURATION"
                ),
            },
        )
        # без указания доски ISS вернёт строку на каждый режим торгов —
        # оставляем ту, где реально есть цифры
        sec = _best_by_secid(_rows(payload, "securities"), ("PREVLEGALCLOSEPRICE", "PREVPRICE"))
        md = _best_by_secid(_rows(payload, "marketdata"), ("LAST", "LCLOSEPRICE", "MARKETPRICE"))

        result: dict[str, dict[str, Any]] = {}
        for secid in secids:
            s = sec.get(secid) or {}
            m = md.get(secid) or {}
            if not s and not m:
                continue
            prev = (
                _to_float(s.get("PREVPRICE"))
                or _to_float(s.get("PREVLEGALCLOSEPRICE"))
                or _to_float(s.get("PREVADMITTEDQUOTE"))
            )
            last = (
                _to_float(m.get("LAST"))
                or _to_float(m.get("LCURRENTPRICE"))
                or _to_float(m.get("MARKETPRICE"))
                or _to_float(m.get("WAPRICE"))
                or _to_float(m.get("LCLOSEPRICE"))
                or prev
            )
            if last is None:
                continue
            # LASTCHANGEPRCNT после закрытия сессии обнуляется, а LASTTOPREVPRICE
            # остаётся изменением к предыдущей цене — именно его показывает брокер
            change = _to_float(m.get("LASTTOPREVPRICE"))
            if change is None and prev:
                change = (last / prev - 1) * 100
            result[secid] = {
                "secid": secid,
                "price": last,
                "prev_close": prev,
                "change_pct": change,
                "accrued_interest": _to_float(s.get("ACCRUEDINT")),
                "face_value": _to_float(s.get("FACEVALUE")),
                "bid": _to_float(m.get("BID")),
                "ask": _to_float(m.get("OFFER")),
                "yield": _to_float(m.get("YIELD")),
                "duration": _to_float(m.get("DURATION")),
                "updated_at": m.get("UPDATETIME"),
                "coupon_value": _to_float(s.get("COUPONVALUE")),
                "next_coupon": _to_date(s.get("NEXTCOUPON")),
            }
        return result

    # --------------------------------------------------------------- история

    async def history(
        self,
        engine: str,
        market: str,
        secid: str,
        start: date,
        end: date,
        board: str | None = None,
    ) -> list[dict[str, Any]]:
        """Дневные свечи из архива ISS с постраничным обходом."""
        path = (
            f"/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
            if board
            else f"/history/engines/{engine}/markets/{market}/securities/{secid}.json"
        )
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._get(
                path,
                {
                    "from": start.isoformat(),
                    "till": end.isoformat(),
                    "start": offset,
                    "iss.only": "history",
                    "history.columns": (
                        "TRADEDATE,CLOSE,LEGALCLOSEPRICE,MARKETPRICE2,WAPRICE,"
                        "ACCINT,FACEVALUE,VOLUME,VALUE"
                    ),
                },
            )
            rows = _rows(payload, "history")
            if not rows:
                break
            for row in rows:
                trade_date = _to_date(row.get("TRADEDATE"))
                close = (
                    _to_float(row.get("CLOSE"))
                    or _to_float(row.get("LEGALCLOSEPRICE"))
                    or _to_float(row.get("MARKETPRICE2"))
                    or _to_float(row.get("WAPRICE"))
                )
                if trade_date is None or close is None:
                    continue
                out.append(
                    {
                        "date": trade_date,
                        "close": close,
                        "accrued_interest": _to_float(row.get("ACCINT")),
                        "face_value": _to_float(row.get("FACEVALUE")),
                        "volume": _to_float(row.get("VALUE")) or _to_float(row.get("VOLUME")),
                    }
                )
            if len(rows) < 100:
                break
            offset += len(rows)
            if offset > 20000:  # предохранитель
                break
            await asyncio.sleep(0.05)  # не душим ISS длинной серией страниц
        out.sort(key=lambda r: r["date"])
        return out

    async def index_history(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        return await self.history("stock", "index", code, start, end)

    # ------------------------------------------------------------ облигации

    async def bondization(self, secid: str) -> dict[str, list[dict[str, Any]]]:
        """Купонный график, амортизации и оферты по облигации."""
        payload = await self._get(
            f"/securities/{secid}/bondization.json", {"limit": "unlimited"}
        )
        coupons = [
            {
                "date": _to_date(r.get("coupondate")),
                "record_date": _to_date(r.get("recorddate")),
                "start_date": _to_date(r.get("startdate")),
                "value": _to_float(r.get("value")) or _to_float(r.get("value_rub")),
                "rate": _to_float(r.get("valueprc")),
                "face_value": _to_float(r.get("facevalue")),
                "currency": _normalize_currency(r.get("faceunit")),
            }
            for r in _rows(payload, "coupons")
        ]
        amortizations = [
            {
                "date": _to_date(r.get("amortdate")),
                "value": _to_float(r.get("value")) or _to_float(r.get("value_rub")),
                "percent": _to_float(r.get("valueprc")),
                "face_value": _to_float(r.get("facevalue")),
                "currency": _normalize_currency(r.get("faceunit")),
            }
            for r in _rows(payload, "amortizations")
        ]
        offers = [
            {
                "date": _to_date(r.get("offerdate")),
                "price": _to_float(r.get("price")),
                "type": r.get("offertype"),
            }
            for r in _rows(payload, "offers")
        ]
        return {
            "coupons": [c for c in coupons if c["date"]],
            "amortizations": [a for a in amortizations if a["date"]],
            "offers": [o for o in offers if o["date"]],
        }

    # ---------------------------------------------------------- дивиденды

    async def dividends(self, secid: str) -> list[dict[str, Any]]:
        payload = await self._get(f"/securities/{secid}/dividends.json")
        out = []
        for row in _rows(payload, "dividends"):
            d = _to_date(row.get("registryclosedate"))
            value = _to_float(row.get("value"))
            if d is None or value is None:
                continue
            out.append(
                {
                    "record_date": d,
                    "value": value,
                    "currency": _normalize_currency(row.get("currencyid")),
                }
            )
        out.sort(key=lambda r: r["record_date"])
        return out


def _best_by_secid(
    rows: list[dict[str, Any]], value_columns: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Схлопывает строки по SECID, предпочитая ту, где заполнены ценовые колонки."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        secid = row.get("SECID")
        if not secid:
            continue
        current = out.get(secid)
        if current is None:
            out[secid] = row
            continue
        has_new = any(_to_float(row.get(c)) is not None for c in value_columns)
        has_old = any(_to_float(current.get(c)) is not None for c in value_columns)
        if has_new and not has_old:
            out[secid] = row
    return out


def _normalize_currency(value: Any) -> str:
    if not value:
        return "RUB"
    v = str(value).upper()
    return {"SUR": "RUB", "RUR": "RUB", "RUB": "RUB"}.get(v, v)


def normalize_price(asset_class: str, raw_price: float, face_value: float | None) -> float:
    """Приводит котировку к цене одной бумаги в валюте.

    Облигации на Мосбирже котируются в процентах от номинала — переводим в рубли.
    """
    if asset_class == "BOND" and face_value:
        return raw_price / 100.0 * face_value
    return raw_price
