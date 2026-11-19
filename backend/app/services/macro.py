"""Макроэкономические показатели для дашборда.

Источники — только бесплатные и без ключей:

* ЦБ РФ, DailyInfo SOAP (``KeyRateXML``) — ключевая ставка;
* ЦБ РФ, XLSX-выгрузка «Инфляция и ключевая ставка» — инфляция г/г и цель;
* ЦБ РФ, XLSX «Показатели сезонно сглаженной динамики потребительских цен» —
  инфляция м/м с сезонной корректировкой;
* MOEX ISS, кривая бескупонной доходности (``zcyc``) — доходность ОФЗ.

Недельная инфляция не выводится: ЦБ публикует её только в PDF и новостях,
без стабильного машинно-читаемого API. Все запросы кешируются; если источник
не ответил, соответствующий показатель просто не попадает в ответ, а дашборд
продолжает работать.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)

#: id выгрузки базы «Инфляция и ключевая ставка» на cbr.ru
CBR_MONTHLY_EXPORT = "/Queries/UniDbQuery/DownloadExcel/132934"
#: XLSX с месячными темпами прироста цен (сезонность устранена)
CBR_CPD_XLSX = "/Content/Document/File/108632/indicators_cpd.xlsx"

#: как долго держим неудачный ответ, чтобы не долбить источники
FAILURE_TTL_SECONDS = 300
#: частичный ответ (часть источников не ответила) обновляем быстрее обычного
PARTIAL_TTL_SECONDS = 900

_cache: tuple[float, dict] | None = None  # (момент истечения, данные)
_cache_lock = asyncio.Lock()


# --------------------------------------------------------------- XLSX (stdlib)


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    value = 0
    for ch in letters.group(1):
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def _read_xlsx_rows(content: bytes) -> list[list[Any]]:
    """Минимальное чтение XLSX без внешних зависимостей.

    Нам нужны только простые таблицы ЦБ: строки, числа и inline/shared-строки.
    """
    archive = zipfile.ZipFile(io.BytesIO(content))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for node in root:
            if _strip_ns(node.tag) != "si":
                continue
            shared.append(
                "".join(t.text or "" for t in node.iter() if _strip_ns(t.tag) == "t")
            )

    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in archive.namelist():
        candidates = sorted(
            n for n in archive.namelist()
            if n.startswith("xl/worksheets/") and n.endswith(".xml")
        )
        if not candidates:
            return []
        sheet_name = candidates[0]

    root = ET.fromstring(archive.read(sheet_name))
    rows: list[list[Any]] = []
    for row in root.iter():
        if _strip_ns(row.tag) != "row":
            continue
        cells: dict[int, Any] = {}
        for cell in row:
            if _strip_ns(cell.tag) != "c":
                continue
            index = _column_index(cell.get("r", ""))
            kind = cell.get("t") or "n"
            value: Any = None
            if kind == "inlineStr":
                value = "".join(
                    t.text or "" for t in cell.iter() if _strip_ns(t.tag) == "t"
                )
            else:
                raw = next(
                    (v for v in cell if _strip_ns(v.tag) == "v"), None
                )
                if raw is not None:
                    text = raw.text or ""
                    if kind == "s":
                        try:
                            value = shared[int(text)]
                        except (ValueError, IndexError):
                            value = None
                    elif kind == "b":
                        value = text == "1"
                    elif kind in ("str", "e"):
                        value = text
                    else:
                        try:
                            value = float(text)
                        except ValueError:
                            value = text
            cells[index] = value
        if cells:
            rows.append([cells.get(i) for i in range(max(cells) + 1)])
    return rows


def _as_float(row: list[Any], index: int | None) -> float | None:
    if index is None or index < 0 or index >= len(row):
        return None
    value = row[index]
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


def _header_index(header: list[Any], prefix: str, default: int) -> int:
    for i, value in enumerate(header):
        if isinstance(value, str) and value.strip().startswith(prefix):
            return i
    return default


def _parse_monthly_export(content: bytes) -> dict | None:
    """Инфляция г/г, цель и (запасной) ключевая ставка из месячной выгрузки."""
    rows = _read_xlsx_rows(content)
    if len(rows) < 2:
        return None
    header = rows[0]
    i_date = _header_index(header, "Дата", 0)
    i_key = _header_index(header, "Ключевая ставка", 1)
    i_infl = _header_index(header, "Инфляция", 2)
    i_target = _header_index(header, "Цель", 3)

    data: list[dict] = []
    for row in rows[1:]:
        if i_date >= len(row):
            continue
        raw = row[i_date]
        if not isinstance(raw, str):
            continue
        match = re.fullmatch(r"(\d{1,2})\.(\d{4})", raw.strip())
        if not match:
            continue
        data.append(
            {
                "date": f"{match.group(2)}-{int(match.group(1)):02d}",
                "key_rate": _as_float(row, i_key),
                "inflation": _as_float(row, i_infl),
                "target": _as_float(row, i_target),
            }
        )
    if not data:
        return None
    data.sort(key=lambda item: item["date"], reverse=True)

    inflation = next((item for item in data if item["inflation"] is not None), None)
    if inflation is None:
        return None
    previous = next(
        (
            item for item in data
            if item["inflation"] is not None and item["date"] < inflation["date"]
        ),
        None,
    )
    key_rate = next((item for item in data if item["key_rate"] is not None), None)

    return {
        "inflation": {
            "value": inflation["inflation"],
            "date": inflation["date"],
            "target": inflation["target"],
            "previous": previous["inflation"] if previous else None,
        },
        "key_rate_fallback": (
            {"value": key_rate["key_rate"], "date": key_rate["date"]}
            if key_rate
            else None
        ),
    }


def _excel_date(serial: float) -> date:
    return date(1899, 12, 30) + timedelta(days=int(serial))


def _parse_cpd_monthly(content: bytes) -> dict | None:
    """Инфляция м/м с сезонной корректировкой из XLSX ЦБ."""
    rows = _read_xlsx_rows(content)
    if len(rows) < 2:
        return None
    header = rows[0]
    row = next(
        (
            candidate for candidate in rows[1:]
            if candidate
            and isinstance(candidate[0], str)
            and candidate[0].strip().startswith("Все товары")
        ),
        None,
    )
    if row is None:
        # CBR иногда меняет подписи — «Все товары и услуги» всегда вторая строка
        row = rows[1] if len(rows) > 1 else None
    if row is None:
        return None

    numeric = [
        i for i, value in enumerate(row)
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    ]
    if not numeric:
        return None
    last = numeric[-1]
    value = float(row[last])

    month: str | None = None
    if last < len(header):
        head = header[last]
        if isinstance(head, (int, float)) and head > 0:
            month = _excel_date(float(head)).strftime("%Y-%m")
        elif isinstance(head, str):
            match = re.fullmatch(r"(\d{1,2})\.(\d{4})", head.strip())
            if match:
                month = f"{match.group(2)}-{int(match.group(1)):02d}"
    return {"value": value, "date": month, "seasonally_adjusted": True}


# ------------------------------------------------------------------- CBR / MOEX


def _parse_key_rate(text: str) -> dict | None:
    """Разбирает SOAP-ответ KeyRateXML в последнюю ставку и её изменение."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    items: list[tuple[date, float]] = []
    for node in root.iter():
        if _strip_ns(node.tag) != "KR":
            continue
        day_text = rate_text = None
        for child in node:
            tag = _strip_ns(child.tag)
            if tag == "DT":
                day_text = (child.text or "").strip()
            elif tag == "Rate":
                rate_text = (child.text or "").strip()
        if not day_text or not rate_text:
            continue
        try:
            day = datetime.fromisoformat(day_text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                day = date.fromisoformat(day_text[:10])
            except ValueError:
                continue
        try:
            rate = float(rate_text.replace(",", "."))
        except ValueError:
            continue
        items.append((day, rate))

    if not items:
        return None
    items.sort(key=lambda item: item[0])
    latest_day, latest_rate = items[-1]

    # ставка действует непрерывным отрезком: находим его начало
    index = len(items) - 1
    while index > 0 and items[index - 1][1] == latest_rate:
        index -= 1
    previous = items[index - 1][1] if index > 0 else None
    return {
        "value": latest_rate,
        "date": items[index][0].isoformat(),
        "as_of": latest_day.isoformat(),
        "previous": previous,
        "change": (latest_rate - previous) if previous is not None else None,
    }


def _moex_rows(payload: dict, block: str) -> list[dict[str, Any]]:
    node = payload.get(block) or {}
    columns = node.get("columns") or []
    return [dict(zip(columns, row)) for row in node.get("data") or []]


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_zcyc(payload: dict) -> dict | None:
    """Доходность ОФЗ из кривой бескупонной доходности Мосбиржи."""
    rows = _moex_rows(payload, "yearyields")
    points: list[dict] = []
    for row in rows:
        tenor = _to_float(row.get("period"))
        value = _to_float(row.get("value"))
        if tenor is None or value is None:
            continue
        points.append(
            {
                "tenor": tenor,
                "value": value,
                "date": row.get("tradedate"),
            }
        )
    if not points:
        return None
    points.sort(key=lambda item: item["tenor"])

    target = float(settings.macro_ofz_tenor)
    exact = next(
        (item for item in points if abs(item["tenor"] - target) < 1e-6), None
    )
    if exact is None:
        exact = min(points, key=lambda item: abs(item["tenor"] - target))

    curve = []
    for tenor in sorted({1.0, 5.0, target}):
        point = next(
            (item for item in points if abs(item["tenor"] - tenor) < 1e-6), None
        )
        if point is None:
            point = min(points, key=lambda item: abs(item["tenor"] - tenor))
        curve.append({"tenor": point["tenor"], "value": point["value"]})

    return {
        "tenor": exact["tenor"],
        "value": exact["value"],
        "date": exact.get("date") or date.today().isoformat(),
        "curve": curve,
    }


# ---------------------------------------------------------------- HTTP-загрузка


def _cbr_base() -> str:
    return settings.macro_cbr_base.rstrip("/")


async def _fetch_key_rate(client: httpx.AsyncClient) -> dict | None:
    start = (date.today() - timedelta(days=400)).isoformat()
    end = date.today().isoformat()
    soap = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <KeyRateXML xmlns="http://web.cbr.ru/">
      <fromDate>{start}T00:00:00</fromDate>
      <ToDate>{end}T00:00:00</ToDate>
    </KeyRateXML>
  </soap:Body>
</soap:Envelope>"""
    response = await client.post(
        f"{_cbr_base()}/DailyInfoWebServ/DailyInfo.asmx",
        content=soap.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://web.cbr.ru/KeyRateXML",
        },
    )
    response.raise_for_status()
    return _parse_key_rate(response.text)


async def _fetch_monthly_export(client: httpx.AsyncClient) -> dict | None:
    today = date.today()
    response = await client.get(
        f"{_cbr_base()}{CBR_MONTHLY_EXPORT}",
        params={
            "FromDate": (today - timedelta(days=730)).strftime("%m/%d/%Y"),
            "ToDate": today.strftime("%m/%d/%Y"),
            "posted": "False",
            "backUrl": "/hd_base/infl/",
        },
    )
    response.raise_for_status()
    return _parse_monthly_export(response.content)


async def _fetch_cpd_monthly(client: httpx.AsyncClient) -> dict | None:
    response = await client.get(f"{_cbr_base()}{CBR_CPD_XLSX}")
    response.raise_for_status()
    return _parse_cpd_monthly(response.content)


async def _fetch_ofz(client: httpx.AsyncClient) -> dict | None:
    response = await client.get(
        f"{settings.moex_iss_base.rstrip('/')}/engines/stock/zcyc.json",
        params={"iss.meta": "off"},
    )
    response.raise_for_status()
    return _parse_zcyc(response.json())


# ------------------------------------------------------------------- сборка


def _empty(now: datetime) -> dict:
    return {
        "available": False,
        "updated_at": now.isoformat(),
        "key_rate": None,
        "inflation": None,
        "inflation_monthly": None,
        "ofz": None,
        "sources": [],
        "notes": [],
    }


async def _load_macro() -> dict:
    now = datetime.now(timezone.utc)
    payload = _empty(now)
    if not settings.macro_enabled:
        payload["disabled"] = True
        payload["notes"].append(
            "Макроблок отключён в настройках (MACRO_ENABLED=false)."
        )
        return payload

    async with httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": "investment-manager/1.0"},
    ) as client:
        results = await asyncio.gather(
            _fetch_key_rate(client),
            _fetch_monthly_export(client),
            _fetch_cpd_monthly(client),
            _fetch_ofz(client),
            return_exceptions=True,
        )

    names = ("ключевая ставка", "инфляция", "инфляция м/м", "доходность ОФЗ")
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            log.warning("макро, %s: %s", name, result)

    key_rate, monthly, cpd, ofz = results

    if isinstance(key_rate, dict):
        payload["key_rate"] = key_rate
        payload["sources"].append("ЦБ РФ · ключевая ставка")

    if isinstance(monthly, dict):
        payload["inflation"] = monthly.get("inflation")
        payload["sources"].append("ЦБ РФ · инфляция")
        fallback = monthly.get("key_rate_fallback")
        if payload["key_rate"] is None and fallback:
            payload["key_rate"] = {
                "value": fallback["value"],
                "date": fallback["date"],
                "as_of": None,
                "previous": None,
                "change": None,
            }
            payload["sources"].append("ЦБ РФ · ключевая ставка (месячные данные)")

    if isinstance(cpd, dict):
        payload["inflation_monthly"] = cpd
        payload["sources"].append("ЦБ РФ · инфляция м/м (сезонно сглаженная)")

    if isinstance(ofz, dict):
        payload["ofz"] = ofz
        payload["sources"].append("MOEX ISS · кривая бескупонной доходности")

    payload["available"] = any(
        payload[key] for key in ("key_rate", "inflation", "inflation_monthly", "ofz")
    )

    if payload["inflation"] is None:
        payload["notes"].append("Инфляция г/г недоступна: выгрузка ЦБ не прочиталась.")
    if payload["inflation_monthly"] is None:
        payload["notes"].append("Инфляция м/м недоступна: выгрузка ЦБ не прочиталась.")
    if payload["ofz"] is None:
        payload["notes"].append("Доходность ОФЗ недоступна: MOEX ISS не ответил.")
    return payload


async def macro_snapshot(force: bool = False) -> dict:
    """Текущие макропоказатели с кешем.

    Успешный ответ живёт ``MACRO_CACHE_MINUTES``, неудачный — 5 минут,
    чтобы при временном сбое источников дашборд быстро восстановился.
    """
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now < _cache[0]:
        return _cache[1]

    async with _cache_lock:
        now = time.monotonic()
        if not force and _cache is not None and now < _cache[0]:
            return _cache[1]

        try:
            data = await _load_macro()
        except Exception as exc:  # noqa: BLE001
            log.warning("макро: не удалось собрать показатели: %s", exc)
            data = _empty(datetime.now(timezone.utc))
            data["notes"].append("Не удалось загрузить макроданные.")

        ttl = _ttl_seconds(data)
        _cache = (time.monotonic() + ttl, data)
        return data


def _ttl_seconds(data: dict) -> int:
    """Полный ответ кешируем долго, частичный — быстрее, пустой — совсем недолго."""
    keys = ("key_rate", "inflation", "inflation_monthly", "ofz")
    if all(data.get(key) is not None for key in keys):
        return max(int(settings.macro_cache_minutes * 60), 60)
    if data.get("available"):
        return PARTIAL_TTL_SECONDS
    return FAILURE_TTL_SECONDS
