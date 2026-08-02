"""Доходности и дюрация облигаций.

Все расчёты идут от денежного потока на одну бумагу: купоны + амортизации +
погашение остатка номинала, дисконтированные к «грязной» цене (чистая + НКД).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

EPS = 1e-12
DAYS_IN_YEAR = 365.0


@dataclass
class CashFlow:
    date: date
    amount: float          # на одну бумагу
    kind: str              # COUPON | AMORTIZATION | MATURITY


@dataclass
class BondYields:
    clean_price: float
    accrued_interest: float
    dirty_price: float
    face_value: float
    current_yield: float | None       # текущая доходность
    simple_yield: float | None        # простая доходность к погашению
    effective_yield: float | None     # эффективная (с реинвестированием купонов)
    macaulay_duration_years: float | None
    macaulay_duration_days: int | None
    modified_duration: float | None
    days_to_maturity: int | None
    maturity_date: date | None
    coupons_per_year: int | None
    annual_coupon: float | None
    total_future_cash: float
    is_estimate: bool = False         # в потоке есть прогнозные купоны флоатера


def annual_coupon_amount(flows: list[CashFlow], as_of: date) -> float | None:
    """Купонные выплаты за ближайшие 12 месяцев."""
    horizon_days = 365
    coupons = [
        cf.amount
        for cf in flows
        if cf.kind == "COUPON" and 0 < (cf.date - as_of).days <= horizon_days
    ]
    return sum(coupons) if coupons else None


def npv(rate: float, flows: list[CashFlow], as_of: date) -> float:
    total = 0.0
    for cf in flows:
        t = (cf.date - as_of).days / DAYS_IN_YEAR
        if t <= 0:
            continue
        total += cf.amount / (1.0 + rate) ** t
    return total


def solve_effective_yield(
    flows: list[CashFlow], dirty_price: float, as_of: date
) -> float | None:
    """Ставка, при которой приведённый поток равен грязной цене."""
    if dirty_price <= EPS or not flows:
        return None
    future = [cf for cf in flows if (cf.date - as_of).days > 0]
    if not future:
        return None

    def objective(rate: float) -> float:
        return npv(rate, future, as_of) - dirty_price

    lo, hi = -0.95, 10.0
    f_lo, f_hi = objective(lo), objective(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = objective(mid)
        if abs(f_mid) < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def durations(
    flows: list[CashFlow], rate: float, as_of: date, dirty_price: float
) -> tuple[float | None, float | None]:
    """Дюрация Маколея (в годах) и модифицированная дюрация."""
    if dirty_price <= EPS or rate is None:
        return None, None
    weighted = 0.0
    present = 0.0
    for cf in flows:
        t = (cf.date - as_of).days / DAYS_IN_YEAR
        if t <= 0:
            continue
        pv = cf.amount / (1.0 + rate) ** t
        weighted += t * pv
        present += pv
    if present <= EPS:
        return None, None
    macaulay = weighted / present
    modified = macaulay / (1.0 + rate)
    return macaulay, modified


def compute_bond_yields(
    flows: list[CashFlow],
    clean_price: float,
    accrued_interest: float,
    face_value: float,
    as_of: date,
    maturity_date: date | None,
    coupons_per_year: int | None = None,
    has_forecast: bool = False,
) -> BondYields:
    dirty = clean_price + accrued_interest
    future = sorted((cf for cf in flows if (cf.date - as_of).days > 0), key=lambda c: c.date)
    total_future = sum(cf.amount for cf in future)

    annual_coupon = annual_coupon_amount(flows, as_of)
    if annual_coupon is None and coupons_per_year and future:
        next_coupon = next((cf.amount for cf in future if cf.kind == "COUPON"), None)
        if next_coupon is not None:
            annual_coupon = next_coupon * coupons_per_year

    cur_yield = (annual_coupon / clean_price) if annual_coupon and clean_price > EPS else None

    days_left = (maturity_date - as_of).days if maturity_date else None
    simple = None
    if days_left and days_left > 0 and dirty > EPS:
        simple = (total_future - dirty) / dirty * (DAYS_IN_YEAR / days_left)

    effective = solve_effective_yield(future, dirty, as_of)
    mac, mod = durations(future, effective, as_of, dirty) if effective is not None else (None, None)

    return BondYields(
        clean_price=clean_price,
        accrued_interest=accrued_interest,
        dirty_price=dirty,
        face_value=face_value,
        current_yield=cur_yield,
        simple_yield=simple,
        effective_yield=effective,
        macaulay_duration_years=mac,
        macaulay_duration_days=int(round(mac * DAYS_IN_YEAR)) if mac is not None else None,
        modified_duration=mod,
        days_to_maturity=days_left,
        maturity_date=maturity_date,
        coupons_per_year=coupons_per_year,
        annual_coupon=annual_coupon,
        total_future_cash=total_future,
        is_estimate=has_forecast,
    )


def price_from_yield(flows: list[CashFlow], rate: float, as_of: date) -> float:
    return npv(rate, flows, as_of)


def yield_to_offer(
    flows: list[CashFlow],
    clean_price: float,
    accrued_interest: float,
    offer_date: date,
    offer_price: float,
    as_of: date,
) -> float | None:
    """Доходность к оферте: поток обрезается датой оферты, в конце — выкуп."""
    trimmed = [cf for cf in flows if as_of < cf.date <= offer_date]
    trimmed.append(CashFlow(date=offer_date, amount=offer_price, kind="MATURITY"))
    return solve_effective_yield(trimmed, clean_price + accrued_interest, as_of)


def accrued_interest_estimate(
    coupon_value: float, period_days: int, coupon_date: date, as_of: date
) -> float:
    """НКД, если биржа его не отдала: линейное накопление внутри купонного периода."""
    if not coupon_value or not period_days:
        return 0.0
    days_left = (coupon_date - as_of).days
    elapsed = max(period_days - days_left, 0)
    return coupon_value * min(elapsed / period_days, 1.0)


def portfolio_bond_summary(items: list[tuple[BondYields, float]]) -> dict:
    """Взвешенные по рыночной стоимости доходность и дюрация по всем облигациям."""
    total_weight = sum(w for _, w in items if w > 0)
    if total_weight <= EPS:
        return {
            "market_value": 0.0,
            "weighted_effective_yield": None,
            "weighted_current_yield": None,
            "weighted_duration_years": None,
            "weighted_duration_days": None,
            "positions": 0,
        }

    def weighted(getter) -> float | None:
        acc = 0.0
        weight = 0.0
        for y, w in items:
            value = getter(y)
            if value is None or w <= 0:
                continue
            acc += value * w
            weight += w
        return acc / weight if weight > EPS else None

    dur = weighted(lambda y: y.macaulay_duration_years)
    return {
        "market_value": total_weight,
        "weighted_effective_yield": weighted(lambda y: y.effective_yield),
        "weighted_current_yield": weighted(lambda y: y.current_yield),
        "weighted_duration_years": dur,
        "weighted_duration_days": int(round(dur * DAYS_IN_YEAR)) if dur is not None else None,
        "positions": len([1 for _, w in items if w > 0]),
    }


def sanitize(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value
