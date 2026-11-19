"""Тесты зачисления выплат: маппинг типов и количество бумаг на дату."""
from datetime import date, datetime

import pytest

from app.enums import PaymentKind, TxType
from app.services.payments import (
    AUTO_KINDS,
    PAID_WINDOW_DAYS,
    UnsupportedPaymentError,
    _ExistingPayment,
    _find_existing,
    compatible_types,
    payment_external_id,
    payment_type,
    quantity_at,
)
from app.services.portfolio import DaySnapshot, LedgerResult


def _ledger() -> LedgerResult:
    return LedgerResult(
        positions={},
        cash=0.0,
        snapshots=[
            DaySnapshot(date(2025, 1, 10), {1: 10.0}, {}, 0.0, 0.0, 0.0, 0.0),
            DaySnapshot(date(2025, 3, 1), {1: 4.0}, {}, 0.0, 0.0, 0.0, 0.0),
        ],
        external_flows=[],
    )


def test_quantity_at_uses_last_snapshot_not_after_date():
    ledger = _ledger()
    assert quantity_at(ledger, 1, date(2025, 1, 1)) == 0
    assert quantity_at(ledger, 1, date(2025, 1, 10)) == 10
    assert quantity_at(ledger, 1, date(2025, 2, 1)) == 10
    assert quantity_at(ledger, 1, date(2025, 3, 1)) == 4
    assert quantity_at(ledger, 2, date(2025, 3, 1)) == 0


def test_quantity_at_without_date_uses_final_position():
    assert quantity_at(_ledger(), 1) == 0


def test_payment_type_mapping():
    assert payment_type(PaymentKind.COUPON.value) == TxType.COUPON.value
    assert payment_type(PaymentKind.DIVIDEND.value) == TxType.DIVIDEND.value
    assert payment_type(PaymentKind.AMORTIZATION.value) == TxType.AMORTIZATION.value
    assert payment_type(PaymentKind.MATURITY.value) == TxType.AMORTIZATION.value
    assert payment_type(PaymentKind.CUSTOM_INCOME.value) == TxType.INTEREST.value
    with pytest.raises(UnsupportedPaymentError):
        payment_type(PaymentKind.OFFER.value)


def test_payment_external_id_is_stable_and_unique():
    first = payment_external_id(PaymentKind.COUPON.value, 7, date(2026, 11, 19))
    assert first == "payment:COUPON:7:2026-11-19"
    assert first == payment_external_id("COUPON", 7, date(2026, 11, 19))
    assert first != payment_external_id(
        PaymentKind.AMORTIZATION.value, 7, date(2026, 11, 19)
    )


def test_coupon_matches_dividend_imports():
    assert compatible_types(TxType.COUPON.value) == (
        TxType.COUPON.value,
        TxType.DIVIDEND.value,
    )
    assert compatible_types(TxType.AMORTIZATION.value) == (TxType.AMORTIZATION.value,)


def _existing(tx_type: str, when: datetime) -> dict:
    return {
        (1, 2, tx_type): [
            _ExistingPayment(id=7, executed_at=when, quantity=10, amount=100, tax=0)
        ]
    }


def test_find_existing_matches_coupon_paid_as_dividend():
    existing = _existing(TxType.DIVIDEND.value, datetime(2026, 1, 12))
    found = _find_existing(existing, 1, 2, TxType.COUPON.value, date(2026, 1, 10))
    assert found is not None and found.id == 7


def test_find_existing_ignores_dates_outside_window():
    existing = _existing(
        TxType.COUPON.value,
        datetime(2026, 1, 10 + PAID_WINDOW_DAYS + 1),
    )
    assert _find_existing(existing, 1, 2, TxType.COUPON.value, date(2026, 1, 10)) is None


def test_auto_post_covers_only_confirmed_bond_payments():
    assert AUTO_KINDS == {
        PaymentKind.COUPON.value,
        PaymentKind.AMORTIZATION.value,
        PaymentKind.MATURITY.value,
    }
    assert PaymentKind.DIVIDEND.value not in AUTO_KINDS
    assert PaymentKind.CUSTOM_INCOME.value not in AUTO_KINDS
