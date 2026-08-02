"""Импорт истории операций из CSV.

Ожидаемые колонки:
    Event,Date,Symbol,Price,Quantity,Currency,FeeTax,Exchange,NKD,
    FeeCurrency,DoNotAdjustCash,Note

Тонкость реальных выгрузок: у событий выплат колонка Quantity заполнена
непоследовательно — иногда это количество бумаг, иногда сама сумма выплаты,
а иногда сумма продублирована в Price. Поэтому сумма выплаты определяется
эвристикой с опорой на количество бумаг в позиции на дату выплаты
(см. ``resolve_payment_amount``), а результат показывается в предпросмотре
до записи в базу.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import groupby

from app.enums import TxType

log = logging.getLogger(__name__)

EXPECTED_HEADER = [
    "Event", "Date", "Symbol", "Price", "Quantity", "Currency",
    "FeeTax", "Exchange", "NKD", "FeeCurrency", "DoNotAdjustCash", "Note",
]

#: событие в файле -> внутренний тип операции
EVENT_MAP = {
    "BUY": TxType.BUY,
    "SELL": TxType.SELL,
    "DIVIDEND": TxType.DIVIDEND,
    "COUPON": TxType.COUPON,
    "AMORTISATION": TxType.AMORTIZATION,
    "AMORTIZATION": TxType.AMORTIZATION,
    "SPLIT": TxType.SPLIT,
    "TAX": TxType.TAX,
    "FEE": TxType.FEE,
    "COMMISSION": TxType.FEE,
    "INTEREST": TxType.INTEREST,
    "CASH_IN": TxType.DEPOSIT,
    "CASH-IN": TxType.DEPOSIT,
    "DEPOSIT": TxType.DEPOSIT,
    "CASH_OUT": TxType.WITHDRAWAL,
    "CASH-OUT": TxType.WITHDRAWAL,
    "WITHDRAWAL": TxType.WITHDRAWAL,
}

PAYMENT_EVENTS = {TxType.DIVIDEND, TxType.COUPON, TxType.AMORTIZATION, TxType.INTEREST}
CASH_EVENTS = {TxType.DEPOSIT, TxType.WITHDRAWAL, TxType.TAX, TxType.FEE}


class ImportError_(ValueError):
    """Ошибка разбора файла."""


@dataclass
class ParsedRow:
    line: int
    event: str
    type: str
    executed_at: datetime
    symbol: str | None
    price: float
    quantity: float
    amount: float
    fee: float
    accrued_interest: float
    currency: str
    do_not_adjust_cash: bool
    note: str | None
    external_id: str
    warnings: list[str] = field(default_factory=list)
    #: как была определена сумма выплаты — показывается в предпросмотре
    amount_rule: str | None = None

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "event": self.event,
            "type": self.type,
            "executed_at": self.executed_at.isoformat(),
            "symbol": self.symbol,
            "price": self.price,
            "quantity": self.quantity,
            "amount": self.amount,
            "fee": self.fee,
            "accrued_interest": self.accrued_interest,
            "currency": self.currency,
            "note": self.note,
            "warnings": self.warnings,
            "amount_rule": self.amount_rule,
            "external_id": self.external_id,
        }


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    errors: list[dict]
    symbols: list[str]
    total_lines: int

    @property
    def ok(self) -> int:
        return len(self.rows)


def parse_number(raw: str | None) -> float:
    """В выгрузках числа приходят с запятой и иногда с разделителями разрядов."""
    if raw is None:
        return 0.0
    text = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if not text or text in ("-", "—"):
        return 0.0
    # десятичный разделитель — запятая; точка встречается в англоязычных выгрузках
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError as exc:
        raise ImportError_(f"не число: {raw!r}") from exc


def parse_bool(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in ("true", "1", "yes", "да")


def parse_datetime(raw: str) -> datetime:
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ImportError_(f"не дата: {raw!r}")


def resolve_payment_amount(
    price: float, quantity: float, shares_held: float
) -> tuple[float, str]:
    """Определяет сумму выплаты по строке отчёта.

    Возвращает (сумма, описание правила).
    """
    if quantity in (0.0, None):
        return price, "сумма из Price (Quantity пуст)"
    if abs(price - quantity) < 0.005:
        # сумма продублирована в обеих колонках
        return price, "Price = Quantity, взята как итоговая сумма"

    candidates = {
        "Quantity как сумма выплаты": quantity,
        "Price × Quantity (Quantity — число бумаг)": price * quantity,
    }
    if shares_held > 0:
        target = price * shares_held
        label, value = min(candidates.items(), key=lambda kv: abs(kv[1] - target))
        return value, f"{label}; сверено с позицией {shares_held:g} шт."

    # позиция неизвестна: целое небольшое Quantity почти всегда = число бумаг
    if quantity == int(quantity) and 0 < quantity < 10000:
        return price * quantity, "Price × Quantity (Quantity выглядит как число бумаг)"
    return quantity, "Quantity как сумма выплаты"


def _external_id(row: list[str], occurrence: int) -> str:
    digest = hashlib.sha1("|".join(row).encode("utf-8")).hexdigest()[:24]
    return f"csv:{digest}:{occurrence}"


def parse_csv(content: bytes | str) -> ParseResult:
    """Разбирает файл целиком, не обращаясь к базе."""
    if isinstance(content, bytes):
        text = _decode(content)
    else:
        text = content
    text = text.lstrip("﻿")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ImportError_("файл пуст") from exc

    header = [h.strip().lstrip("﻿") for h in header]
    missing = [c for c in ("Event", "Date", "Symbol", "Price", "Quantity") if c not in header]
    if missing:
        raise ImportError_(
            f"в заголовке нет обязательных колонок: {', '.join(missing)}. "
            f"Ожидаемый набор колонок: {','.join(EXPECTED_HEADER)}"
        )
    index = {name: i for i, name in enumerate(header)}

    raw_rows: list[tuple[int, list[str]]] = []
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        raw_rows.append((line_no, row))

    def cell(row: list[str], name: str) -> str:
        i = index.get(name)
        if i is None or i >= len(row):
            return ""
        return row[i].strip()

    # первый проход: разбор без сумм выплат
    staged: list[dict] = []
    errors: list[dict] = []
    seen: dict[str, int] = defaultdict(int)

    for line_no, row in raw_rows:
        try:
            event_raw = cell(row, "Event").upper()
            tx_type = EVENT_MAP.get(event_raw)
            if tx_type is None:
                raise ImportError_(f"неизвестное событие {event_raw!r}")

            executed_at = parse_datetime(cell(row, "Date"))
            symbol = cell(row, "Symbol").upper() or None
            price = parse_number(cell(row, "Price"))
            quantity = parse_number(cell(row, "Quantity"))
            fee = parse_number(cell(row, "FeeTax"))
            nkd = parse_number(cell(row, "NKD"))
            currency = (cell(row, "Currency") or "RUB").upper()
            if currency in ("SUR", "RUR"):
                currency = "RUB"

            key = "|".join(row)
            seen[key] += 1
            staged.append(
                {
                    "line": line_no,
                    "event": event_raw,
                    "type": tx_type,
                    "executed_at": executed_at,
                    "symbol": symbol,
                    "price": price,
                    "quantity": quantity,
                    "fee": fee,
                    "nkd": nkd,
                    "currency": currency,
                    "dnac": parse_bool(cell(row, "DoNotAdjustCash")),
                    "note": cell(row, "Note") or None,
                    "external_id": _external_id(row, seen[key]),
                }
            )
        except ImportError_ as exc:
            errors.append({"line": line_no, "error": str(exc), "raw": ",".join(row)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"line": line_no, "error": f"{type(exc).__name__}: {exc}", "raw": ",".join(row)})

    # второй проход: считаем позиции по датам, чтобы разобрать суммы выплат.
    # Выплату сверяем с позицией на начало дня — купон часто приходит в тот же
    # день, когда бумага уже продана.
    staged.sort(key=lambda r: (r["executed_at"], r["line"]))
    holdings: dict[str, float] = defaultdict(float)
    rows: list[ParsedRow] = []

    for _, group in groupby(staged, key=lambda r: r["executed_at"].date()):
        day_items = list(group)
        start_holdings = dict(holdings)
        sell_warnings: dict[int, list[str]] = {}

        for item in day_items:
            symbol = item["symbol"]
            if not symbol:
                continue
            if item["type"] == TxType.BUY:
                holdings[symbol] += item["quantity"]
            elif item["type"] == TxType.SELL:
                if item["quantity"] > holdings[symbol] + 1e-9:
                    sell_warnings.setdefault(item["line"], []).append(
                        f"продажа {item['quantity']:g} шт. при позиции {holdings[symbol]:g} шт."
                    )
                holdings[symbol] = max(holdings[symbol] - item["quantity"], 0.0)
            elif item["type"] == TxType.SPLIT and (item["price"] or 0) > 0:
                holdings[symbol] *= item["price"]

        for item in day_items:
            tx_type: TxType = item["type"]
            symbol = item["symbol"]
            price = item["price"]
            quantity = item["quantity"]
            warnings: list[str] = list(sell_warnings.get(item["line"], []))
            amount_rule = None
            amount = price * quantity

            if tx_type == TxType.SPLIT:
                amount = 0.0
                quantity = 0.0
                warnings.append(f"сплит с коэффициентом {price:g}")
            elif tx_type in PAYMENT_EVENTS:
                key = symbol or ""
                held = start_holdings.get(key, 0.0) or holdings.get(key, 0.0)
                amount, amount_rule = resolve_payment_amount(price, quantity, held)
                if held <= 0:
                    warnings.append("на дату выплаты позиция нулевая — проверьте сумму")
            elif tx_type in CASH_EVENTS:
                amount = price * quantity if quantity else price

            rows.append(
            ParsedRow(
                line=item["line"],
                event=item["event"],
                type=tx_type.value,
                executed_at=item["executed_at"],
                symbol=symbol,
                price=price,
                quantity=quantity,
                amount=round(amount, 8),
                fee=item["fee"],
                accrued_interest=item["nkd"],
                currency=item["currency"],
                do_not_adjust_cash=item["dnac"],
                note=item["note"],
                external_id=item["external_id"],
                warnings=warnings,
                amount_rule=amount_rule,
            )
        )

    symbols = sorted({r.symbol for r in rows if r.symbol})
    return ParseResult(rows=rows, errors=errors, symbols=symbols, total_lines=len(raw_rows))


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 8)))
