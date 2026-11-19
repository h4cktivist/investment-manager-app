"""Тесты расчётного ядра: FIFO-учёт, метрики, доходности облигаций, импорт."""
from datetime import date, datetime, timedelta

import pytest

from app.services import bond_math, metrics as m
from app.services.importers.broker_csv import parse_csv, parse_number, resolve_payment_amount
from app.services.portfolio import (
    PriceBook,
    ValuationPoint,
    build_valuation_series,
    replay,
    resample_to_trading_days,
)


class FakeTx:
    """Заменяет модель Transaction — движку нужны только эти поля."""

    _seq = 0

    def __init__(
        self,
        type: str,
        day: str,
        price: float = 0,
        quantity: float = 0,
        amount: float = 0,
        fee: float = 0,
        tax: float = 0,
        accrued_interest: float = 0,
        asset_id: int | None = 1,
    ) -> None:
        FakeTx._seq += 1
        self.id = FakeTx._seq
        self.type = type
        self.executed_at = datetime.fromisoformat(f"{day}T00:00:00")
        self.price = price
        self.quantity = quantity
        self.amount = amount or price * quantity
        self.fee = fee
        self.tax = tax
        self.accrued_interest = accrued_interest
        self.asset_id = asset_id
        self.do_not_adjust_cash = False


# ------------------------------------------------------------------ FIFO


def test_fifo_realized_pnl_uses_oldest_lot_first():
    ledger = replay(
        [
            FakeTx("BUY", "2025-01-10", price=100, quantity=10),
            FakeTx("BUY", "2025-02-10", price=120, quantity=10),
            FakeTx("SELL", "2025-03-10", price=130, quantity=10),
        ]
    )
    pos = ledger.positions[1]
    # продали первый лот по 100 -> прибыль 30 на бумагу
    assert pos.realized_pnl == pytest.approx(300)
    assert pos.qty == pytest.approx(10)
    # остался лот по 120
    assert pos.avg_price == pytest.approx(120)


def test_fees_and_nkd_land_in_cost_basis():
    ledger = replay([FakeTx("BUY", "2025-01-10", price=1000, quantity=2, fee=6, accrued_interest=14)])
    pos = ledger.positions[1]
    assert pos.cost_basis == pytest.approx(2020)  # 2000 + 6 комиссии + 14 НКД
    assert pos.avg_clean_price == pytest.approx(1000)


def test_split_multiplies_quantity_and_divides_price():
    ledger = replay(
        [
            FakeTx("BUY", "2025-01-10", price=3000, quantity=2),
            FakeTx("SPLIT", "2025-06-01", price=10),
        ]
    )
    pos = ledger.positions[1]
    assert pos.qty == pytest.approx(20)
    assert pos.avg_price == pytest.approx(300)
    assert pos.cost_basis == pytest.approx(6000)  # стоимость позиции не меняется


def test_amortization_reduces_cost_basis_not_quantity():
    ledger = replay(
        [
            FakeTx("BUY", "2025-01-10", price=1000, quantity=10),
            FakeTx("AMORTIZATION", "2025-06-01", amount=500),
        ]
    )
    pos = ledger.positions[1]
    assert pos.qty == pytest.approx(10)
    assert pos.cost_basis == pytest.approx(9500)
    assert pos.amortization == pytest.approx(500)


def test_dividend_net_of_tax_goes_to_cash():
    ledger = replay(
        [
            FakeTx("BUY", "2025-01-10", price=100, quantity=10),
            FakeTx("DIVIDEND", "2025-05-10", amount=200, tax=26),
        ]
    )
    assert ledger.dividends == pytest.approx(174)
    assert ledger.cash == pytest.approx(174)


def test_missing_deposits_are_auto_funded():
    """В выгрузках брокеров пополнений нет — иначе денежный остаток уйдёт в минус."""
    ledger = replay([FakeTx("BUY", "2025-01-10", price=100, quantity=10, fee=1)])
    assert ledger.cash == pytest.approx(0)
    assert ledger.total_deposits == pytest.approx(1001)
    assert ledger.external_flows == [(date(2025, 1, 10), pytest.approx(1001))]


def test_explicit_deposit_is_not_double_counted():
    ledger = replay(
        [
            FakeTx("DEPOSIT", "2025-01-09", amount=5000, asset_id=None),
            FakeTx("BUY", "2025-01-10", price=100, quantity=10),
        ]
    )
    assert ledger.total_deposits == pytest.approx(5000)
    assert ledger.cash == pytest.approx(4000)


# ------------------------------------------------------------- переоценка


def _book(prices: dict[str, float], asset_id: int = 1) -> PriceBook:
    return PriceBook(
        series={asset_id: [(date.fromisoformat(d), p) for d, p in sorted(prices.items())]},
        live={},
    )


def test_position_without_quote_is_valued_at_cost_not_zero():
    ledger = replay([FakeTx("BUY", "2025-01-10", price=100, quantity=10)])
    points = build_valuation_series(
        ledger, PriceBook(series={}, live={}), start=date(2025, 1, 10), end=date(2025, 1, 12)
    )
    assert all(p.total == pytest.approx(1000) for p in points)


def test_valuation_follows_market_price():
    ledger = replay([FakeTx("BUY", "2025-01-10", price=100, quantity=10)])
    book = _book({"2025-01-10": 100, "2025-01-11": 110})
    points = build_valuation_series(ledger, book, start=date(2025, 1, 10), end=date(2025, 1, 11))
    assert points[0].total == pytest.approx(1000)
    assert points[1].total == pytest.approx(1100)


def test_resample_moves_weekend_flows_to_next_trading_day():
    points = [
        ValuationPoint(date(2025, 3, 7), 100, 0, 100, 0, 100, 0),    # пятница
        ValuationPoint(date(2025, 3, 8), 100, 0, 100, 50, 100, 5),   # суббота
        ValuationPoint(date(2025, 3, 10), 160, 0, 160, 0, 150, 0),   # понедельник
    ]
    trading = {date(2025, 3, 7), date(2025, 3, 10)}
    out = resample_to_trading_days(points, trading)
    assert [p.day for p in out] == [date(2025, 3, 7), date(2025, 3, 10)]
    assert out[1].external_flow == pytest.approx(50)
    assert out[1].income == pytest.approx(5)


# ---------------------------------------------------------------- метрики


def _points(values: list[tuple[str, float, float]]) -> list[ValuationPoint]:
    return [
        ValuationPoint(date.fromisoformat(d), total, 0, total, flow, total, 0)
        for d, total, flow in values
    ]


def test_twr_ignores_deposits():
    """Внесение денег не должно выглядеть как доходность."""
    points = _points([("2025-01-01", 100, 100), ("2025-01-02", 200, 100), ("2025-01-03", 220, 0)])
    series = m.twr_returns(points)
    # день 2: (200 - 100 внесённых) / 100 = 0 %; день 3: +10 %
    assert series.daily[1] == pytest.approx(0.0)
    assert series.daily[2] == pytest.approx(0.1)
    assert series.total == pytest.approx(0.1)


def test_twr_uses_opening_value_for_partial_period():
    """Без стоимости на начало периода короткий интервал давал тысячи процентов."""
    points = _points([("2025-06-01", 10_000, 40), ("2025-06-02", 10_100, 0)])
    naive = m.twr_returns(points)
    correct = m.twr_returns(points, opening_value=9_990)
    assert naive.total > 100          # старая ошибка: старт от платежа 40 ₽
    # день 1: 10000 / (9990 + 40) − 1 = −0,299 %; день 2: +1 %
    assert correct.daily[0] == pytest.approx(-0.002991, abs=1e-5)
    assert correct.daily[1] == pytest.approx(0.01, abs=1e-6)
    assert correct.total == pytest.approx(0.006979, abs=1e-5)


def test_annualize_skipped_for_short_windows():
    assert m.annualize(0.02, 14) is None
    assert m.annualize(0.10, 365) == pytest.approx(0.10, abs=1e-3)


def test_xirr_matches_known_case():
    # вложили 1000, через год получили 1100 -> 10 % годовых
    rate = m.xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)])
    assert rate == pytest.approx(0.10, abs=1e-3)


def test_xirr_accounts_for_opening_balance():
    rate = m.money_weighted_return(
        external_flows=[],
        final_value=1100,
        as_of=date(2026, 1, 1),
        opening_value=1000,
        opening_date=date(2025, 1, 1),
    )
    assert rate == pytest.approx(0.10, abs=1e-3)


def test_volatility_and_sortino_ignore_upside():
    daily = [0.01, -0.02, 0.03, -0.01, 0.005]
    assert m.volatility(daily) > 0
    # у ряда без падений просадочная волатильность нулевая -> Сортино не определён
    assert m.sortino(0.2, [0.01] * 10, 0.0) is None


def test_beta_of_identical_series_is_one():
    series = [0.01, -0.02, 0.03, -0.01, 0.005, 0.002]
    beta, _, corr = m.beta_alpha(series, series)
    assert beta == pytest.approx(1.0)
    assert corr == pytest.approx(1.0)


def test_max_drawdown_finds_worst_peak_to_trough():
    index = [1.0, 1.2, 0.9, 1.0, 0.8, 1.1]
    dd, start, end = m.max_drawdown(index)
    assert dd == pytest.approx(0.8 / 1.2 - 1.0)
    assert (start, end) == (1, 4)


def test_historical_var_and_expected_shortfall():
    daily = [0.01, -0.05, 0.02, -0.01, -0.03, 0.005, -0.02, 0.0, 0.015, -0.04]
    # позиция 9 × 5 % = 0,45 → интерполяция между −5 % и −4 %
    assert m.historical_var(daily, 0.95) == pytest.approx(-0.0455)
    # за границей VaR остаётся только худший день
    assert m.expected_shortfall(daily, 0.95) == pytest.approx(-0.05)
    assert m.historical_var([0.01], 0.95) is None


def test_risk_values_annualize_by_root_time():
    assert m.annualize_risk(-0.01) == pytest.approx(-0.01 * 252 ** 0.5)
    assert m.annualize_risk(None) is None


def test_calmar_is_return_per_unit_of_drawdown():
    assert m.calmar(0.20, -0.10) == pytest.approx(2.0)
    assert m.calmar(0.20, 0.0) is None
    assert m.calmar(None, -0.10) is None


def test_compute_metrics_exposes_tail_risk_and_extremes():
    day = date(2025, 1, 1)
    total = 10_000.0
    points = [ValuationPoint(day, total, 0, total, 0, total, 0)]
    moves = [0.01, -0.02, 0.005, -0.03, 0.02] * 15  # 75 торговых дней
    for offset, move in enumerate(moves, start=1):
        total *= 1 + move
        points.append(
            ValuationPoint(day + timedelta(days=offset), total, 0, total, 0, total, 0)
        )

    bundle = m.compute_metrics(points, [], total, risk_free=0.0)
    assert bundle.observations == len(points)
    assert bundle.var_95_daily is not None
    assert bundle.var_99_daily is not None
    # ES не может быть мягче VaR: это средний убыток за его границей
    assert bundle.es_95_daily <= bundle.var_95_daily
    assert bundle.worst_day == pytest.approx(-0.03)
    assert bundle.worst_day_date is not None
    assert bundle.best_day == pytest.approx(0.02)
    assert bundle.best_day_date is not None


# ------------------------------------------------------------- облигации


def _annual_bond(years: int, coupon: float, face: float = 1000) -> list[bond_math.CashFlow]:
    today = date(2026, 1, 1)
    flows = [
        bond_math.CashFlow(today + timedelta(days=365 * i), coupon, "COUPON")
        for i in range(1, years + 1)
    ]
    flows.append(bond_math.CashFlow(today + timedelta(days=365 * years), face, "MATURITY"))
    return flows


def test_bond_priced_at_par_yields_its_coupon():
    flows = _annual_bond(3, 100)
    y = bond_math.compute_bond_yields(
        flows, clean_price=1000, accrued_interest=0, face_value=1000,
        as_of=date(2026, 1, 1), maturity_date=date(2028, 12, 31), coupons_per_year=1,
    )
    assert y.current_yield == pytest.approx(0.10, abs=1e-3)
    assert y.effective_yield == pytest.approx(0.10, abs=2e-3)


def test_discount_bond_yields_more_than_coupon():
    flows = _annual_bond(3, 100)
    y = bond_math.compute_bond_yields(
        flows, clean_price=900, accrued_interest=0, face_value=1000,
        as_of=date(2026, 1, 1), maturity_date=date(2028, 12, 31), coupons_per_year=1,
    )
    assert y.effective_yield > 0.10
    assert y.current_yield == pytest.approx(100 / 900, abs=1e-4)


def test_duration_is_shorter_than_maturity_and_below_it_for_coupon_bond():
    flows = _annual_bond(3, 100)
    y = bond_math.compute_bond_yields(
        flows, clean_price=1000, accrued_interest=0, face_value=1000,
        as_of=date(2026, 1, 1), maturity_date=date(2028, 12, 31), coupons_per_year=1,
    )
    assert 2.0 < y.macaulay_duration_years < 3.0
    assert y.modified_duration < y.macaulay_duration_years


def test_zero_coupon_duration_equals_maturity():
    flows = [bond_math.CashFlow(date(2029, 1, 1), 1000, "MATURITY")]
    y = bond_math.compute_bond_yields(
        flows, clean_price=700, accrued_interest=0, face_value=1000,
        as_of=date(2026, 1, 1), maturity_date=date(2029, 1, 1),
    )
    assert y.macaulay_duration_years == pytest.approx(3.0, abs=0.02)


def test_accrued_interest_is_part_of_price_for_yield():
    flows = _annual_bond(2, 100)
    clean = bond_math.compute_bond_yields(
        flows, 1000, 0, 1000, date(2026, 1, 1), date(2027, 12, 31), 1
    )
    dirty = bond_math.compute_bond_yields(
        flows, 1000, 50, 1000, date(2026, 1, 1), date(2027, 12, 31), 1
    )
    assert dirty.effective_yield < clean.effective_yield


# ---------------------------------------------------------------- импорт


def test_parse_number_handles_russian_decimals():
    assert parse_number("1355,8") == pytest.approx(1355.8)
    assert parse_number("1 355,80") == pytest.approx(1355.8)
    assert parse_number("") == 0.0


@pytest.mark.parametrize(
    "price,quantity,held,expected",
    [
        (30.77, 30.77, 1, 30.77),    # сумма продублирована в обеих колонках
        (29.01, 1, 1, 29.01),        # Quantity — число бумаг
        (19.15, 57.45, 3, 57.45),    # Quantity — уже сумма выплаты
        (17.26, 155.34, 0, 155.34),  # позиция закрыта, дробное число = сумма
        (59.82, 59.82, 1000, 59.82),
    ],
)
def test_payment_amount_resolution(price, quantity, held, expected):
    amount, rule = resolve_payment_amount(price, quantity, held)
    assert amount == pytest.approx(expected)
    assert rule


def test_parse_csv_reads_expected_columns():
    csv = (
        "Event,Date,Symbol,Price,Quantity,Currency,FeeTax,Exchange,NKD,"
        "FeeCurrency,DoNotAdjustCash,Note\n"
        'BUY,2025-07-22 00:00:00,RU000A10C5K9,"1000","3",RUB,"24",MCX,"0","RUB","False",""\n'
        'DIVIDEND,2025-08-21 00:00:00,RU000A10C5K9,"19,15","57,45",RUB,"0",MCX,"","RUB","False",""\n'
        'SPLIT,2026-04-16 03:00:00,T,"10","0",RUB,"0",MCX,"0","","",""\n'
    )
    result = parse_csv(csv)
    assert not result.errors
    assert [r.type for r in result.rows] == ["BUY", "DIVIDEND", "SPLIT"]
    assert result.rows[0].amount == pytest.approx(3000)
    assert result.rows[0].fee == pytest.approx(24)
    assert result.rows[1].amount == pytest.approx(57.45)
    assert result.symbols == ["RU000A10C5K9", "T"]


def test_parse_csv_rejects_foreign_header():
    with pytest.raises(Exception):
        parse_csv("foo,bar\n1,2\n")


def test_duplicate_rows_get_distinct_ids():
    line = 'BUY,2025-07-22 00:00:00,SBER,"300","1",RUB,"1",MCX,"0","RUB","False",""\n'
    header = (
        "Event,Date,Symbol,Price,Quantity,Currency,FeeTax,Exchange,NKD,"
        "FeeCurrency,DoNotAdjustCash,Note\n"
    )
    result = parse_csv(header + line + line)
    assert len({r.external_id for r in result.rows}) == 2
