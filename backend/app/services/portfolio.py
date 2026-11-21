"""Учётное ядро: FIFO-лоты, денежный остаток, переоценка портфеля по дням."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import PaymentKind, TxType
from app.models import Asset, PaymentSchedule, PriceHistory, Quote, Transaction

log = logging.getLogger(__name__)

EPS = 1e-9


def f(value: Decimal | float | int | None) -> float:
    return float(value or 0)


@dataclass
class Lot:
    qty: float
    price: float       # «чистая» цена покупки
    unit_cost: float   # цена с учётом комиссии и уплаченного НКД
    opened_at: date


@dataclass
class Position:
    asset_id: int
    qty: float = 0.0
    lots: list[Lot] = field(default_factory=list)
    realized_pnl: float = 0.0
    dividends: float = 0.0        # чистыми, после налога
    coupons: float = 0.0
    amortization: float = 0.0
    fees: float = 0.0
    taxes: float = 0.0
    buy_turnover: float = 0.0
    sell_turnover: float = 0.0
    first_trade: date | None = None
    last_trade: date | None = None

    @property
    def cost_basis(self) -> float:
        return sum(lot.qty * lot.unit_cost for lot in self.lots)

    @property
    def avg_price(self) -> float:
        if self.qty <= EPS:
            return 0.0
        return self.cost_basis / self.qty

    @property
    def avg_clean_price(self) -> float:
        if self.qty <= EPS:
            return 0.0
        return sum(lot.qty * lot.price for lot in self.lots) / self.qty

    @property
    def income(self) -> float:
        return self.dividends + self.coupons


@dataclass
class DaySnapshot:
    day: date
    qty: dict[int, float]
    cost: dict[int, float]  # себестоимость за бумагу — запасная оценка без котировки
    cash: float
    external_flow: float   # + внесение денег, − вывод
    income: float          # выплаты, полученные за день (нетто)
    invested: float        # накопленные вложения по себестоимости


@dataclass
class LedgerResult:
    positions: dict[int, Position]
    cash: float
    snapshots: list[DaySnapshot]
    external_flows: list[tuple[date, float]]
    total_fees: float = 0.0
    total_taxes: float = 0.0
    total_deposits: float = 0.0
    total_withdrawals: float = 0.0
    realized_pnl: float = 0.0
    dividends: float = 0.0
    coupons: float = 0.0
    amortization: float = 0.0
    interest: float = 0.0

    @property
    def start_date(self) -> date | None:
        return self.snapshots[0].day if self.snapshots else None


def replay(transactions: list[Transaction], auto_fund: bool = True) -> LedgerResult:
    """Проигрывает сделки по порядку и строит позиции + дневные снимки.

    ``auto_fund`` — если денег на счёте не хватает на покупку (типичная ситуация,
    когда пользователь не заводит операции пополнения), недостающая сумма
    считается внешним пополнением. Без этого TWR и XIRR посчитать нельзя.
    """
    txs = sorted(transactions, key=lambda t: (t.executed_at, t.id or 0))
    positions: dict[int, Position] = {}
    cash = 0.0
    snapshots: list[DaySnapshot] = []
    external_flows: list[tuple[date, float]] = []
    result = LedgerResult(positions=positions, cash=0.0, snapshots=snapshots, external_flows=[])

    if not txs:
        return result

    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for tx in txs:
        by_day[tx.executed_at.date()].append(tx)

    invested = 0.0

    for day in sorted(by_day):
        flow_today = 0.0
        income_today = 0.0

        for tx in by_day[day]:
            tx_type = tx.type
            qty = f(tx.quantity)
            price = f(tx.price)
            amount = f(tx.amount)
            fee = f(tx.fee)
            tax = f(tx.tax)
            nkd = f(tx.accrued_interest)
            pos = None
            if tx.asset_id is not None:
                pos = positions.setdefault(tx.asset_id, Position(asset_id=tx.asset_id))

            if tx_type == TxType.BUY.value and pos is not None:
                gross = amount or price * qty
                # НКД указывается за единицу — в общую стоимость входит nkd × qty
                total_cost = gross + fee + nkd * qty
                if qty > EPS:
                    pos.lots.append(
                        Lot(
                            qty=qty,
                            price=price,
                            unit_cost=total_cost / qty,
                            opened_at=day,
                        )
                    )
                    pos.qty += qty
                pos.fees += fee
                pos.buy_turnover += gross
                pos.first_trade = pos.first_trade or day
                pos.last_trade = day
                result.total_fees += fee
                invested += total_cost
                if not tx.do_not_adjust_cash:
                    cash -= total_cost

            elif tx_type == TxType.SELL.value and pos is not None:
                gross = amount or price * qty
                proceeds = gross - fee + nkd * qty
                cost_removed = _consume_fifo(pos, qty)
                pos.realized_pnl += proceeds - cost_removed
                pos.fees += fee
                pos.taxes += tax
                pos.sell_turnover += gross
                pos.last_trade = day
                result.total_fees += fee
                result.total_taxes += tax
                invested -= cost_removed
                if not tx.do_not_adjust_cash:
                    cash += proceeds - tax

            elif tx_type in (TxType.DIVIDEND.value, TxType.COUPON.value):
                net = (amount or price * qty) - tax
                if pos is not None:
                    if tx_type == TxType.DIVIDEND.value:
                        pos.dividends += net
                    else:
                        pos.coupons += net
                    pos.taxes += tax
                if tx_type == TxType.DIVIDEND.value:
                    result.dividends += net
                else:
                    result.coupons += net
                result.total_taxes += tax
                income_today += net
                if not tx.do_not_adjust_cash:
                    cash += net

            elif tx_type == TxType.AMORTIZATION.value:
                net = (amount or price * qty) - tax
                if pos is not None:
                    pos.amortization += net
                    _reduce_basis(pos, net)
                result.amortization += net
                invested -= net
                income_today += net
                if not tx.do_not_adjust_cash:
                    cash += net

            elif tx_type == TxType.INTEREST.value:
                net = (amount or price * qty) - tax
                result.interest += net
                income_today += net
                if pos is not None:
                    pos.coupons += net
                if not tx.do_not_adjust_cash:
                    cash += net

            elif tx_type == TxType.SPLIT.value and pos is not None:
                ratio = price or 1.0
                if ratio > 0:
                    for lot in pos.lots:
                        lot.qty *= ratio
                        lot.price /= ratio
                        lot.unit_cost /= ratio
                    pos.qty *= ratio

            elif tx_type == TxType.DEPOSIT.value:
                value = amount or price * qty
                cash += value
                flow_today += value
                result.total_deposits += value

            elif tx_type == TxType.WITHDRAWAL.value:
                value = abs(amount or price * qty)
                cash -= value
                flow_today -= value
                result.total_withdrawals += value

            elif tx_type == TxType.FEE.value:
                value = abs(amount or price * qty) or fee
                cash -= value
                result.total_fees += value

            elif tx_type == TxType.TAX.value:
                value = abs(amount or price * qty) or tax
                cash -= value
                result.total_taxes += value

        if auto_fund and cash < -EPS:
            shortfall = -cash
            cash = 0.0
            flow_today += shortfall
            result.total_deposits += shortfall

        if abs(flow_today) > EPS:
            external_flows.append((day, flow_today))

        snapshots.append(
            DaySnapshot(
                day=day,
                qty={aid: p.qty for aid, p in positions.items() if abs(p.qty) > EPS},
                cost={
                    aid: p.avg_price for aid, p in positions.items() if abs(p.qty) > EPS
                },
                cash=cash,
                external_flow=flow_today,
                income=income_today,
                invested=invested,
            )
        )

    result.cash = cash
    result.external_flows = external_flows
    result.realized_pnl = sum(p.realized_pnl for p in positions.values())
    return result


def available_quantity(
    transactions: list[Transaction],
    asset_id: int,
    before: tuple[datetime, float],
    exclude_id: int | None = None,
) -> float:
    """Сколько бумаг было в портфеле непосредственно перед сделкой.

    ``before`` — позиция сделки в общем порядке ``(executed_at, id)``. Для новой
    записи вторым элементом передаётся ``inf``: у неё ещё нет id, и все сделки
    того же дня уже состоялись раньше. ``exclude_id`` исключает из расчёта
    редактируемую сделку, чтобы её старое количество не попало в остаток.
    """
    earlier = [
        tx
        for tx in transactions
        if (exclude_id is None or tx.id != exclude_id)
        and (tx.executed_at, tx.id or 0) < before
    ]
    position = replay(earlier).positions.get(asset_id)
    return position.qty if position is not None else 0.0


def _consume_fifo(pos: Position, qty: float) -> float:
    """Списывает qty бумаг по FIFO, возвращает снятую себестоимость."""
    remaining = qty
    cost = 0.0
    while remaining > EPS and pos.lots:
        lot = pos.lots[0]
        take = min(lot.qty, remaining)
        cost += take * lot.unit_cost
        lot.qty -= take
        remaining -= take
        if lot.qty <= EPS:
            pos.lots.pop(0)
    if remaining > EPS:
        # продажа без предшествующей покупки (например, обрезанная история)
        log.debug("продажа %s бумаг без открытых лотов по активу %s", remaining, pos.asset_id)
    pos.qty = max(pos.qty - qty, 0.0)
    return cost


def _reduce_basis(pos: Position, amount: float) -> None:
    """Амортизация номинала уменьшает себестоимость открытых лотов пропорционально."""
    if pos.qty <= EPS or amount <= 0:
        return
    per_unit = amount / pos.qty
    for lot in pos.lots:
        lot.unit_cost = max(lot.unit_cost - per_unit, 0.0)


# --------------------------------------------------------------- переоценка


@dataclass
class PriceBook:
    """Цены всех активов по дням с протяжкой последнего известного значения."""

    series: dict[int, list[tuple[date, float]]]
    live: dict[int, float]

    def value_at(self, asset_id: int, day: date) -> float | None:
        """Последняя котировка на дату или None, если данных нет."""
        points = self.series.get(asset_id)
        if not points or points[0][0] > day:
            return None
        lo, hi = 0, len(points) - 1
        best: float | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if points[mid][0] <= day:
                best = points[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def latest(self, asset_id: int) -> float:
        if asset_id in self.live:
            return self.live[asset_id]
        points = self.series.get(asset_id)
        return points[-1][1] if points else 0.0

    def has_data(self, asset_id: int) -> bool:
        return bool(self.series.get(asset_id)) or asset_id in self.live


async def load_price_book(
    session: AsyncSession, asset_ids: list[int], include_accrued: bool = True
) -> PriceBook:
    """Готовит «грязные» (с НКД) цены за бумагу по дням + последние котировки."""
    if not asset_ids:
        return PriceBook(series={}, live={})

    assets = (
        await session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    ).all()
    by_id = {a.id: a for a in assets}

    rows = (
        await session.execute(
            select(
                PriceHistory.asset_id,
                PriceHistory.date,
                PriceHistory.close_norm,
                PriceHistory.accrued_interest,
            )
            .where(PriceHistory.asset_id.in_(asset_ids))
            .order_by(PriceHistory.asset_id, PriceHistory.date)
        )
    ).all()

    series: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for asset_id, day, close_norm, accint in rows:
        value = f(close_norm)
        if include_accrued and accint is not None:
            value += f(accint)
        series[asset_id].append((day, value))

    quotes = (
        await session.scalars(select(Quote).where(Quote.asset_id.in_(asset_ids)))
    ).all()
    live: dict[int, float] = {}
    for q in quotes:
        value = f(q.price_norm)
        if include_accrued and q.accrued_interest is not None:
            value += f(q.accrued_interest)
        if value:
            live[q.asset_id] = value

    # кастомные активы: ручная цена (+ капитализация процентов)
    today = date.today()
    for asset in assets:
        if not asset.is_custom:
            continue
        start = asset.start_date or today
        points: list[tuple[date, float]] = []
        cursor = start
        while cursor <= today:
            points.append((cursor, custom_asset_price(asset, cursor)))
            cursor += timedelta(days=31)
        points.append((today, custom_asset_price(asset, today)))
        series[asset.id] = points
        live[asset.id] = custom_asset_price(asset, today)

    for asset_id in asset_ids:
        asset = by_id.get(asset_id)
        if asset_id not in series and asset is not None and asset.manual_price:
            series[asset_id] = [(date(1970, 1, 1), f(asset.manual_price))]
            live[asset_id] = f(asset.manual_price)

    return PriceBook(series=dict(series), live=live)


def custom_asset_price(asset: Asset, when: date) -> float:
    """Цена единицы кастомного актива. Для вклада с капитализацией — с ростом."""
    base = f(asset.manual_price) or 1.0
    rate = f(asset.annual_rate) / 100.0
    if not asset.capitalization or not rate or not asset.start_date:
        return base
    if when <= asset.start_date:
        return base
    freq = asset.payout_frequency or "MONTHLY"
    per_year = {
        "MONTHLY": 12,
        "QUARTERLY": 4,
        "SEMIANNUAL": 2,
        "ANNUAL": 1,
        "AT_MATURITY": 1,
        "NONE": 0,
    }.get(freq, 12)
    if per_year == 0:
        return base
    end = asset.end_date or when
    effective = min(when, end)
    years = (effective - asset.start_date).days / 365.25
    return base * (1 + rate / per_year) ** (per_year * years)


@dataclass
class ValuationPoint:
    day: date
    value: float          # рыночная стоимость бумаг
    cash: float
    total: float          # value + cash
    external_flow: float
    invested: float
    income: float


def build_valuation_series(
    ledger: LedgerResult,
    prices: PriceBook,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 1,
) -> list[ValuationPoint]:
    """Ежедневная переоценка портфеля от первой сделки до сегодня."""
    if not ledger.snapshots:
        return []

    first = ledger.snapshots[0].day
    start = max(start or first, first)
    end = end or date.today()
    if end < start:
        return []

    snapshots = ledger.snapshots
    flows_by_day = {s.day: (s.external_flow, s.income) for s in snapshots}
    snap_idx = 0
    current = snapshots[0]
    # доводим состояние до стартовой даты
    while snap_idx + 1 < len(snapshots) and snapshots[snap_idx + 1].day <= start:
        snap_idx += 1
        current = snapshots[snap_idx]

    out: list[ValuationPoint] = []
    day = start
    today = date.today()
    while day <= end:
        while snap_idx + 1 < len(snapshots) and snapshots[snap_idx + 1].day <= day:
            snap_idx += 1
            current = snapshots[snap_idx]

        flow, income = flows_by_day.get(day, (0.0, 0.0))

        holdings_value = 0.0
        for asset_id, qty in current.qty.items():
            if abs(qty) <= EPS:
                continue
            price = prices.latest(asset_id) if day >= today else None
            if not price:
                price = prices.value_at(asset_id, day)
            if price is None:
                # нет котировки на эту дату — оцениваем по себестоимости,
                # иначе позиция «исчезает» и портит доходность
                price = current.cost.get(asset_id, 0.0)
            holdings_value += qty * price

        out.append(
            ValuationPoint(
                day=day,
                value=holdings_value,
                cash=current.cash,
                total=holdings_value + current.cash,
                external_flow=flow,
                invested=current.invested,
                income=income,
            )
        )
        day += timedelta(days=step_days)

    return out


def resample_to_trading_days(
    points: list[ValuationPoint], trading_days: set[date]
) -> list[ValuationPoint]:
    """Оставляет только биржевые дни, перенося потоки выходных на ближайший торговый день.

    Считать волатильность и бету по календарным дням нельзя: выходные дают
    нулевые доходности и занижают риск.
    """
    if not points or not trading_days:
        return points

    out: list[ValuationPoint] = []
    pending_flow = 0.0
    pending_income = 0.0
    for point in points:
        if point.day in trading_days:
            out.append(
                ValuationPoint(
                    day=point.day,
                    value=point.value,
                    cash=point.cash,
                    total=point.total,
                    external_flow=point.external_flow + pending_flow,
                    invested=point.invested,
                    income=point.income + pending_income,
                )
            )
            pending_flow = 0.0
            pending_income = 0.0
        else:
            pending_flow += point.external_flow
            pending_income += point.income

    # хвост нерабочих дней — дописываем последнюю точку, чтобы не потерять потоки
    if (pending_flow or pending_income) and points:
        last = points[-1]
        out.append(
            ValuationPoint(
                day=last.day,
                value=last.value,
                cash=last.cash,
                total=last.total,
                external_flow=pending_flow,
                invested=last.invested,
                income=pending_income,
            )
        )
    return out


# --------------------------------------------------------------- загрузка


async def load_transactions(
    session: AsyncSession,
    portfolio_ids: list[int] | None = None,
    until: date | None = None,
) -> list[Transaction]:
    stmt = select(Transaction).options(selectinload(Transaction.asset))
    if portfolio_ids:
        stmt = stmt.where(Transaction.portfolio_id.in_(portfolio_ids))
    if until:
        stmt = stmt.where(Transaction.executed_at <= _end_of_day(until))
    stmt = stmt.order_by(Transaction.executed_at, Transaction.id)
    return list((await session.scalars(stmt)).all())


def _end_of_day(day: date):
    from datetime import datetime, time

    return datetime.combine(day, time.max)


async def upcoming_payments_for_holdings(
    session: AsyncSession,
    qty_by_asset: dict[int, float],
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Ожидаемые выплаты по текущим позициям в заданном окне."""
    if not qty_by_asset:
        return []
    rows = (
        await session.execute(
            select(PaymentSchedule, Asset)
            .join(Asset, Asset.id == PaymentSchedule.asset_id)
            .where(
                PaymentSchedule.asset_id.in_(list(qty_by_asset)),
                PaymentSchedule.payment_date >= date_from,
                PaymentSchedule.payment_date <= date_to,
                PaymentSchedule.kind != PaymentKind.OFFER.value,
            )
            .order_by(PaymentSchedule.payment_date)
        )
    ).all()

    out = []
    for payment, asset in rows:
        qty = qty_by_asset.get(payment.asset_id, 0.0)
        if qty <= EPS:
            continue
        per_unit = f(payment.value_per_unit)
        out.append(
            {
                "asset_id": asset.id,
                "secid": asset.secid,
                "name": asset.short_name or asset.name,
                "asset_class": asset.asset_class,
                "kind": payment.kind,
                "payment_date": payment.payment_date,
                "record_date": payment.record_date,
                "value_per_unit": per_unit,
                "quantity": qty,
                "amount": per_unit * qty,
                "currency": payment.currency,
                "is_forecast": payment.is_forecast,
                "note": payment.note,
            }
        )
    return out
