"""Зачисление выплат из календаря в сделки.

Купоны, амортизации и погашения по облигациям создаются автоматически, когда
наступает дата выплаты. Дивиденды и доход по кастомным активам автоматически не
проводим: у дивидендов дата выплаты оценочная (отсечка + 10 дней), а у вкладов
сумма зависит от фактического остатка на счёте.

Идемпотентность держится на ``external_id`` вида
``payment:<kind>:<asset_id>:<дата>`` и на проверке уже существующих выплат
в окне нескольких дней. Поэтому повторный запуск фоновой задачи не плодит
дубли, а импорт брокерского отчёта может «подхватить» ранее созданную
автоматическую операцию и заменить её фактическими данными.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.enums import INCOME_TYPES, PaymentKind, TxType
from app.models import PaymentSchedule, Portfolio, Transaction
from app.services.portfolio import EPS, LedgerResult, load_transactions, replay

log = logging.getLogger(__name__)

#: вид выплаты из календаря -> тип операции
PAYMENT_TX_TYPE: dict[str, str] = {
    PaymentKind.COUPON.value: TxType.COUPON.value,
    PaymentKind.DIVIDEND.value: TxType.DIVIDEND.value,
    PaymentKind.AMORTIZATION.value: TxType.AMORTIZATION.value,
    PaymentKind.MATURITY.value: TxType.AMORTIZATION.value,
    PaymentKind.CUSTOM_INCOME.value: TxType.INTEREST.value,
}

#: что проводим автоматически при наступлении даты выплаты
AUTO_KINDS = frozenset(
    {
        PaymentKind.COUPON.value,
        PaymentKind.AMORTIZATION.value,
        PaymentKind.MATURITY.value,
    }
)

#: насколько дата фактической операции может отличаться от плановой
PAID_WINDOW_DAYS = 5

#: источники операций, которые могла создать эта задача (в т.ч. вручную из
#: календаря) и которые импорт отчёта вправе заменить фактическими данными
REPLACEABLE_SOURCES = ("auto", "calendar")

DEFAULT_NOTES = {
    PaymentKind.COUPON.value: "купон по графику выплат",
    PaymentKind.AMORTIZATION.value: "амортизация номинала по графику",
    PaymentKind.MATURITY.value: "погашение облигации по графику",
    PaymentKind.DIVIDEND.value: "дивиденд по графику выплат",
    PaymentKind.CUSTOM_INCOME.value: "доход по активу по графику выплат",
}


class PaymentNotFoundError(LookupError):
    """Выплаты нет в календаре."""


class NoHoldingsError(LookupError):
    """На дату выплаты не было открытой позиции."""


class UnsupportedPaymentError(ValueError):
    """Такой вид выплаты нельзя превратить в сделку."""


@dataclass
class BookedPayment:
    created: bool
    transaction_id: int | None
    portfolio_id: int
    portfolio_name: str
    quantity: float
    amount: float
    tax: float = 0.0


@dataclass
class _ExistingPayment:
    id: int
    executed_at: datetime
    quantity: float
    amount: float
    tax: float


def payment_type(kind: str) -> str:
    tx_type = PAYMENT_TX_TYPE.get(kind)
    if tx_type is None:
        raise UnsupportedPaymentError(f"Выплату вида {kind} нельзя зачислить в сделки")
    return tx_type


def payment_external_id(kind: str, asset_id: int, payment_date: date) -> str:
    return f"payment:{kind}:{asset_id}:{payment_date.isoformat()}"


def compatible_types(tx_type: str) -> tuple[str, ...]:
    """Какими типами операции могла быть записана эта выплата.

    В брокерских выгрузках купоны облигаций иногда помечены как DIVIDEND,
    поэтому при поиске уже зачисленного купона учитываем оба варианта.
    """
    if tx_type == TxType.COUPON.value:
        return (TxType.COUPON.value, TxType.DIVIDEND.value)
    return (tx_type,)


def quantity_at(ledger: LedgerResult, asset_id: int, when: date | None = None) -> float:
    """Количество бумаг в портфеле на конец дня.

    ``when=None`` — на последнюю дату в журнале. Иначе берём последний снимок
    не позже указанной даты; снимки строятся только по дням со сделками.
    """
    if when is None:
        position = ledger.positions.get(asset_id)
        return position.qty if position else 0.0
    qty = 0.0
    for snapshot in ledger.snapshots:
        if snapshot.day > when:
            break
        qty = snapshot.qty.get(asset_id, 0.0)
    return qty


# ------------------------------------------------------------------ портфели


async def _portfolios(
    session: AsyncSession, portfolio_ids: list[int] | None = None
) -> list[Portfolio]:
    # Архивные портфели не отбрасываем: если бумага была в них на дату
    # фиксации реестра, выплата по-прежнему принадлежит этому портфелю.
    stmt = select(Portfolio).order_by(Portfolio.id)
    if portfolio_ids:
        stmt = stmt.where(Portfolio.id.in_(portfolio_ids))
    return list((await session.scalars(stmt)).all())


async def _ledgers_by_portfolio(
    session: AsyncSession,
    portfolios: list[Portfolio],
    until: date | None = None,
) -> dict[int, LedgerResult]:
    transactions = await load_transactions(
        session, [portfolio.id for portfolio in portfolios], until=until
    )
    grouped: dict[int, list[Transaction]] = defaultdict(list)
    for tx in transactions:
        grouped[tx.portfolio_id].append(tx)
    return {
        portfolio.id: replay(grouped.get(portfolio.id, []))
        for portfolio in portfolios
    }


async def holdings_by_portfolio(
    session: AsyncSession,
    asset_id: int,
    when: date,
    portfolio_ids: list[int] | None = None,
) -> dict[int, float]:
    """Сколько бумаг было в каждом портфеле на дату (для права на выплату)."""
    portfolios = await _portfolios(session, portfolio_ids)
    if not portfolios:
        return {}
    ledgers = await _ledgers_by_portfolio(session, portfolios, until=when)
    out: dict[int, float] = {}
    for portfolio in portfolios:
        qty = quantity_at(ledgers[portfolio.id], asset_id)
        if qty > EPS:
            out[portfolio.id] = qty
    return out


# --------------------------------------------------------------- уже зачислено


async def _load_existing(
    session: AsyncSession,
    portfolio_ids: list[int],
    asset_ids: list[int],
    date_from: date,
    date_to: date,
) -> dict[tuple[int, int, str], list[_ExistingPayment]]:
    if not portfolio_ids or not asset_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Transaction.id,
                Transaction.portfolio_id,
                Transaction.asset_id,
                Transaction.type,
                Transaction.executed_at,
                Transaction.quantity,
                Transaction.amount,
                Transaction.tax,
            ).where(
                Transaction.portfolio_id.in_(portfolio_ids),
                Transaction.asset_id.in_(asset_ids),
                Transaction.type.in_([t.value for t in INCOME_TYPES]),
                Transaction.executed_at >= datetime.combine(date_from, time.min),
                Transaction.executed_at <= datetime.combine(date_to, time.max),
            )
        )
    ).all()

    out: dict[tuple[int, int, str], list[_ExistingPayment]] = defaultdict(list)
    for row in rows:
        out[(row.portfolio_id, row.asset_id, row.type)].append(
            _ExistingPayment(
                id=row.id,
                executed_at=row.executed_at,
                quantity=float(row.quantity),
                amount=float(row.amount),
                tax=float(row.tax),
            )
        )
    return out


def _find_existing(
    existing: dict[tuple[int, int, str], list[_ExistingPayment]],
    portfolio_id: int,
    asset_id: int,
    tx_type: str,
    payment_date: date,
) -> _ExistingPayment | None:
    for candidate in compatible_types(tx_type):
        for row in existing.get((portfolio_id, asset_id, candidate), ()):
            if abs((row.executed_at.date() - payment_date).days) <= PAID_WINDOW_DAYS:
                return row
    return None


async def find_auto_payment(
    session: AsyncSession,
    portfolio_id: int,
    asset_id: int,
    tx_type: str,
    when: date,
) -> Transaction | None:
    """Ищет операцию из графика выплат, которую можно заменить импортом."""
    rows = list(
        (
            await session.scalars(
                select(Transaction).where(
                    Transaction.portfolio_id == portfolio_id,
                    Transaction.asset_id == asset_id,
                    Transaction.source.in_(REPLACEABLE_SOURCES),
                    Transaction.type.in_(compatible_types(tx_type)),
                    Transaction.executed_at
                    >= datetime.combine(when - timedelta(days=PAID_WINDOW_DAYS), time.min),
                    Transaction.executed_at
                    <= datetime.combine(when + timedelta(days=PAID_WINDOW_DAYS), time.max),
                )
            )
        ).all()
    )
    if not rows:
        return None
    return min(rows, key=lambda tx: abs((tx.executed_at.date() - when).days))


# ------------------------------------------------------------------- создание


def _build_transaction(
    payment: PaymentSchedule,
    portfolio_id: int,
    qty: float,
    source: str,
) -> Transaction:
    amount = Decimal(str(round(float(payment.value_per_unit) * qty, 8)))
    return Transaction(
        portfolio_id=portfolio_id,
        asset_id=payment.asset_id,
        type=payment_type(payment.kind),
        executed_at=datetime.combine(payment.payment_date, time.min),
        price=payment.value_per_unit,
        quantity=Decimal(str(round(qty, 8))),
        amount=amount,
        fee=0,
        tax=0,
        accrued_interest=0,
        currency=payment.currency,
        note=payment.note or DEFAULT_NOTES.get(payment.kind),
        source=source,
        external_id=payment_external_id(
            payment.kind, payment.asset_id, payment.payment_date
        ),
        do_not_adjust_cash=False,
    )


async def book_schedule_payment(
    session: AsyncSession,
    asset_id: int,
    kind: str,
    payment_date: date,
    portfolio_ids: list[int] | None = None,
) -> list[BookedPayment]:
    """Зачисляет одну выплату из календаря во все портфели, где была позиция.

    Возвращает результат по каждому портфелю. ``created=False`` означает, что
    выплата уже была заведена раньше (импортом, вручную или автозадачей).
    """
    tx_type = payment_type(kind)
    payment = await session.scalar(
        select(PaymentSchedule).where(
            PaymentSchedule.asset_id == asset_id,
            PaymentSchedule.kind == kind,
            PaymentSchedule.payment_date == payment_date,
        )
    )
    if payment is None:
        raise PaymentNotFoundError("Выплата не найдена в календаре")

    # право на купон определяется позицией на дату фиксации реестра
    eligibility_date = payment.record_date or payment.payment_date
    holdings = await holdings_by_portfolio(
        session, asset_id, eligibility_date, portfolio_ids
    )
    if not holdings:
        raise NoHoldingsError(
            "На дату выплаты не было открытой позиции — зачислять нечего"
        )

    portfolios = await _portfolios(session, list(holdings))
    names = {portfolio.id: portfolio.name for portfolio in portfolios}
    existing = await _load_existing(
        session,
        list(holdings),
        [asset_id],
        payment_date - timedelta(days=PAID_WINDOW_DAYS),
        payment_date + timedelta(days=PAID_WINDOW_DAYS),
    )

    results: list[BookedPayment] = []
    created_any = False
    for portfolio_id, qty in holdings.items():
        found = _find_existing(existing, portfolio_id, asset_id, tx_type, payment_date)
        if found is not None:
            results.append(
                BookedPayment(
                    created=False,
                    transaction_id=found.id,
                    portfolio_id=portfolio_id,
                    portfolio_name=names.get(portfolio_id, str(portfolio_id)),
                    quantity=found.quantity,
                    amount=found.amount,
                    tax=found.tax,
                )
            )
            continue

        tx = _build_transaction(payment, portfolio_id, qty, source="calendar")
        try:
            async with session.begin_nested():
                session.add(tx)
                await session.flush()
        except IntegrityError:
            # параллельно сработала автозадача и уже создала эту операцию
            log.warning(
                "выплата %s по активу %s в портфеле %s уже зачислена",
                kind,
                asset_id,
                portfolio_id,
            )
            with contextlib.suppress(Exception):
                session.expunge(tx)
            results.append(
                BookedPayment(
                    created=False,
                    transaction_id=None,
                    portfolio_id=portfolio_id,
                    portfolio_name=names.get(portfolio_id, str(portfolio_id)),
                    quantity=qty,
                    amount=float(round(float(payment.value_per_unit) * qty, 8)),
                )
            )
            continue

        created_any = True
        results.append(
            BookedPayment(
                created=True,
                transaction_id=tx.id,
                portfolio_id=portfolio_id,
                portfolio_name=names.get(portfolio_id, str(portfolio_id)),
                quantity=qty,
                amount=float(tx.amount),
                tax=float(tx.tax),
            )
        )

    if created_any:
        await session.commit()
    return results


async def auto_book_due_payments(
    session: AsyncSession,
    today: date | None = None,
    lookback_days: int | None = None,
) -> list[BookedPayment]:
    """Проводит подтверждённые выплаты, дата которых уже наступила.

    ``lookback_days`` ограничивает «догон» после простоя: по умолчанию берём
    настройку из окружения, чтобы не заводить многолетнюю историю при первом
    запуске, если её ещё не импортировали.
    """
    today = today or date.today()
    lookback = max(
        settings.auto_post_lookback_days if lookback_days is None else lookback_days,
        0,
    )
    window_start = today - timedelta(days=lookback)

    payments = list(
        (
            await session.scalars(
                select(PaymentSchedule)
                .where(
                    PaymentSchedule.payment_date >= window_start,
                    PaymentSchedule.payment_date <= today,
                    PaymentSchedule.kind.in_(AUTO_KINDS),
                    PaymentSchedule.is_forecast.is_(False),
                    PaymentSchedule.value_per_unit > 0,
                )
                .order_by(PaymentSchedule.payment_date, PaymentSchedule.id)
            )
        ).all()
    )
    if not payments:
        return []

    portfolios = await _portfolios(session)
    if not portfolios:
        return []

    ledgers = await _ledgers_by_portfolio(session, portfolios)
    names = {portfolio.id: portfolio.name for portfolio in portfolios}
    asset_ids = sorted({payment.asset_id for payment in payments})
    existing = await _load_existing(
        session,
        [portfolio.id for portfolio in portfolios],
        asset_ids,
        window_start - timedelta(days=PAID_WINDOW_DAYS),
        today + timedelta(days=PAID_WINDOW_DAYS),
    )

    created: list[BookedPayment] = []
    for payment in payments:
        tx_type = payment_type(payment.kind)
        eligibility_date = payment.record_date or payment.payment_date
        for portfolio in portfolios:
            qty = quantity_at(
                ledgers[portfolio.id], payment.asset_id, eligibility_date
            )
            if qty <= EPS:
                continue
            if (
                _find_existing(
                    existing,
                    portfolio.id,
                    payment.asset_id,
                    tx_type,
                    payment.payment_date,
                )
                is not None
            ):
                continue

            tx = _build_transaction(payment, portfolio.id, qty, source="auto")
            try:
                async with session.begin_nested():
                    session.add(tx)
                    await session.flush()
            except IntegrityError:
                # параллельный запуск уже создал эту операцию
                log.warning(
                    "выплата %s по активу %s в портфеле %s уже зачислена",
                    payment.kind,
                    payment.asset_id,
                    portfolio.id,
                )
                with contextlib.suppress(Exception):
                    session.expunge(tx)
                continue

            # чтобы следующая выплата того же вида в этом же прогоне не создала
            # дубль (например, амортизация и погашение одной датой)
            existing[(portfolio.id, payment.asset_id, tx.type)].append(
                _ExistingPayment(
                    id=tx.id,
                    executed_at=tx.executed_at,
                    quantity=qty,
                    amount=float(tx.amount),
                    tax=float(tx.tax),
                )
            )
            created.append(
                BookedPayment(
                    created=True,
                    transaction_id=tx.id,
                    portfolio_id=portfolio.id,
                    portfolio_name=names.get(portfolio.id, str(portfolio.id)),
                    quantity=qty,
                    amount=float(tx.amount),
                )
            )
            log.info(
                "автозачисление: %s по %s за %s, портфель %s, %.2f",
                payment.kind,
                payment.asset_id,
                payment.payment_date,
                portfolio.id,
                float(tx.amount),
            )

    if created:
        await session.commit()
    return created


# ------------------------------------------------------------------- фоновый цикл


async def payment_autoposter() -> None:
    """Периодически зачисляет наступившие выплаты, пока приложение работает."""
    if not settings.auto_post_payments:
        log.info("автозачисление выплат выключено (AUTO_POST_PAYMENTS=false)")
        return

    interval = max(settings.auto_post_interval_minutes, 5) * 60
    while True:
        try:
            async with SessionLocal() as session:
                booked = await auto_book_due_payments(session)
            if booked:
                log.info("автозачислено выплат: %s", len(booked))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — фоновая задача не должна падать
            log.warning("автозачисление выплат: %s", exc)
        await asyncio.sleep(interval)
