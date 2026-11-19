"""Тесты разбора макроданных: SOAP ЦБ, XLSX-выгрузки и кривая ОФЗ.

Сеть не используется — проверяются чистые парсеры на синтетических ответах.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.services import macro


def _xlsx(rows: list[list[object]]) -> bytes:
    """Минимальный XLSX с inline-строками и числами — как у выгрузок ЦБ."""
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value is None:
                continue
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            elif isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="n"><v>{value}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')

    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_monthly_export_reads_latest_inflation_and_key_rate():
    content = _xlsx(
        [
            ["Дата", "Ключевая ставка, % годовых", "Инфляция, % г/г", "Цель по инфляции"],
            ["09.2026", 14.0, 6.35, 4.0],
            ["08.2026", 14.0, 6.33, 4.0],
        ]
    )
    parsed = macro._parse_monthly_export(content)
    assert parsed is not None
    assert parsed["inflation"] == {
        "value": 6.35,
        "date": "2026-09",
        "target": 4.0,
        "previous": 6.33,
    }
    assert parsed["key_rate_fallback"] == {"value": 14.0, "date": "2026-09"}


def test_key_rate_soap_finds_segment_start_and_change():
    xml = """<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
      <Body><KeyRateXMLResponse xmlns="http://web.cbr.ru/"><KeyRateXMLResult>
        <KR><DT>2026-06-01T00:00:00+03:00</DT><Rate>15.00</Rate></KR>
        <KR><DT>2026-09-01T00:00:00+03:00</DT><Rate>14.00</Rate></KR>
        <KR><DT>2026-10-01T00:00:00+03:00</DT><Rate>14.00</Rate></KR>
      </KeyRateXMLResult></KeyRateXMLResponse></Body>
    </Envelope>"""
    parsed = macro._parse_key_rate(xml)
    assert parsed is not None
    assert parsed["value"] == 14.0
    assert parsed["date"] == "2026-09-01"
    assert parsed["as_of"] == "2026-10-01"
    assert parsed["previous"] == 15.0
    assert parsed["change"] == pytest.approx(-1.0)


def test_zcyc_picks_configured_tenor_and_builds_curve():
    payload = {
        "yearyields": {
            "columns": ["tradedate", "tradetime", "period", "value"],
            "data": [
                ["2026-11-18", "18:00:00", 0.25, 13.2],
                ["2026-11-18", "18:00:00", 1.0, 12.9],
                ["2026-11-18", "18:00:00", 5.0, 12.5],
                ["2026-11-18", "18:00:00", 10.0, 12.3],
            ],
        }
    }
    parsed = macro._parse_zcyc(payload)
    assert parsed is not None
    assert parsed["tenor"] == 10.0
    assert parsed["value"] == 12.3
    assert {point["tenor"] for point in parsed["curve"]} == {1.0, 5.0, 10.0}


def test_cpd_monthly_reads_seasonally_adjusted_last_month():
    serial = 45000.0  # дата в формате Excel
    content = _xlsx(
        [
            ["Темпы прироста цен, сезонность устранена", serial - 31, serial],
            ["Все товары и услуги", 0.2, 0.31],
            ["Продовольственные товары", 0.1, 0.2],
        ]
    )
    parsed = macro._parse_cpd_monthly(content)
    assert parsed is not None
    assert parsed["value"] == 0.31
    assert parsed["seasonally_adjusted"] is True
    assert parsed["date"] == macro._excel_date(serial).strftime("%Y-%m")


def test_cache_ttl_depends_on_completeness():
    from app.config import settings

    full = {
        "available": True,
        "key_rate": {}, "inflation": {}, "inflation_monthly": {}, "ofz": {},
    }
    assert macro._ttl_seconds(full) == max(int(settings.macro_cache_minutes * 60), 60)
    # один источник отвалился — не держим частичный ответ три часа
    assert macro._ttl_seconds({**full, "ofz": None}) == macro.PARTIAL_TTL_SECONDS
    assert macro._ttl_seconds({"available": False}) == macro.FAILURE_TTL_SECONDS
