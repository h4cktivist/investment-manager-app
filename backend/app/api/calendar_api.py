from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import DB, PortfolioIds
from app.enums import INCOME_TYPES, PaymentKind
from app.models import Asset, Transaction
from app.schemas import (
    BookedPaymentOut,
    BookPaymentRequest,
    BookPaymentResponse,
    CalendarItem,
    CalendarMonth,
    CalendarResponse,
)
from app.services.analytics import PortfolioAnalytics
from app.services.payments import (
    NoHoldingsError,
    PaymentNotFoundError,
    UnsupportedPaymentError,
    auto_book_due_payments,
    book_schedule_payment,
)
from app.services.portfolio import upcoming_payments_for_holdings

router = APIRouter(prefix="/calendar", tags=["Календарь выплат"])

KIND_LABELS = {
    PaymentKind.COUPON.value: "Купон",
    PaymentKind.AMORTIZATION.value: "Амортизация",
    PaymentKind.MATURITY.value: "Погашение",
    PaymentKind.DIVIDEND.value: "Дивиденд",
    PaymentKind.CUSTOM_INCOME.value: "Доход по активу",
}


@router.get("", response_model=CalendarResponse, summary="Календарь ожидаемых выплат")
async def payments_calendar(
    db: DB,
    portfolio_id: PortfolioIds = None,
    date_from: date | None = Query(None, description="По умолчанию сегодня"),
    date_to: date | None = Query(None, description="По умолчанию +12 месяцев"),
    kind: list[str] | None = Query(None, description="COUPON, DIVIDEND, AMORTIZATION, MATURITY"),
):
    today = date.today()
    date_from = date_from or today
    date_to = date_to or (today + timedelta(days=365))

    analytics = await PortfolioAnalytics.load(db, portfolio_id)
    rows = await upcoming_payments_for_holdings(
        db, analytics.open_quantities(), date_from, date_to
    )
    if kind:
        wanted = {k.upper() for k in kind}
        rows = [r for r in rows if r["kind"] in wanted]

    # какие выплаты уже фактически получены — чтобы не показывать их как будущие
    paid = await _paid_keys(db, portfolio_id, date_from, date_to)

    items = [
        CalendarItem(
            **{k: v for k, v in row.items()},
            is_paid=(row["asset_id"], row["payment_date"]) in paid,
        )
        for row in rows
    ]
    items.sort(key=lambda i: (i.payment_date, -i.amount))

    by_month: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for item in items:
        key = item.payment_date.strftime("%Y-%m")
        by_month[key]["amount"] += item.amount
        by_month[key]["count"] += 1

    future = [i for i in items if i.payment_date >= today and not i.is_paid]
    return CalendarResponse(
        date_from=date_from,
        date_to=date_to,
        items=items,
        by_month=[
            CalendarMonth(month=k, amount=round(v["amount"], 2), count=v["count"])
            for k, v in sorted(by_month.items())
        ],
        total_amount=round(sum(i.amount for i in items if not i.is_paid), 2),
        next_payment=future[0] if future else None,
    )


@router.post(
    "/payments/book",
    response_model=BookPaymentResponse,
    summary="Зачислить выплату из календаря в сделки",
)
async def book_payment(payload: BookPaymentRequest, db: DB):
    """Ручное зачисление: создаёт операцию по выплате во всех портфелях,
    где на дату фиксации реестра была открыта позиция.

    Если операция уже была создана автоматически, импортом или вручную,
    повторная запись не делается — в ответе будет ``created=false``.
    """
    try:
        results = await book_schedule_payment(
            db,
            asset_id=payload.asset_id,
            kind=payload.kind.value,
            payment_date=payload.payment_date,
            portfolio_ids=payload.portfolio_ids,
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except NoHoldingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    except UnsupportedPaymentError as exc:
        raise HTTPException(400, str(exc)) from exc

    asset = await db.get(Asset, payload.asset_id)
    return BookPaymentResponse(
        asset_id=payload.asset_id,
        secid=asset.secid if asset else "",
        kind=payload.kind.value,
        payment_date=payload.payment_date,
        items=[BookedPaymentOut(**asdict(item)) for item in results],
    )


@router.post(
    "/payments/auto-post",
    summary="Зачислить все наступившие выплаты",
)
async def auto_post_payments(
    db: DB,
    lookback_days: int | None = Query(None, ge=0, le=3650),
):
    """Досрочно запускает фоновую задачу — например, после ручного импорта
    истории, чтобы не ждать следующего цикла."""
    results = await auto_book_due_payments(db, lookback_days=lookback_days)
    return {
        "posted": len(results),
        "items": [asdict(item) for item in results],
    }


@router.get("/history", summary="Фактически полученные выплаты")
async def payments_history(
    db: DB,
    portfolio_id: PortfolioIds = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 500,
):
    stmt = (
        select(Transaction)
        .where(Transaction.type.in_([t.value for t in INCOME_TYPES]))
        .order_by(Transaction.executed_at.desc())
        .limit(limit)
    )
    if portfolio_id:
        stmt = stmt.where(Transaction.portfolio_id.in_(portfolio_id))
    if date_from:
        stmt = stmt.where(Transaction.executed_at >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.executed_at <= date_to)

    rows = list((await db.scalars(stmt)).all())
    items = [
        {
            "id": tx.id,
            "date": tx.executed_at.date().isoformat(),
            "type": tx.type,
            "type_label": KIND_LABELS.get(tx.type, tx.type),
            "secid": tx.asset.secid if tx.asset else None,
            "name": (tx.asset.short_name or tx.asset.name) if tx.asset else None,
            "amount": float(tx.amount),
            "tax": float(tx.tax),
            "net": float(tx.amount) - float(tx.tax),
            "currency": tx.currency,
        }
        for tx in rows
    ]

    by_month: dict[str, float] = defaultdict(float)
    for item in items:
        by_month[item["date"][:7]] += item["net"]

    return {
        "items": items,
        "total": round(sum(i["net"] for i in items), 2),
        "by_month": [
            {"month": k, "amount": round(v, 2)} for k, v in sorted(by_month.items())
        ],
    }


async def _paid_keys(
    db: DB, portfolio_id: list[int] | None, date_from: date, date_to: date
) -> set[tuple[int, date]]:
    stmt = select(Transaction.asset_id, Transaction.executed_at).where(
        Transaction.type.in_([t.value for t in INCOME_TYPES]),
        Transaction.executed_at >= date_from - timedelta(days=5),
        Transaction.executed_at <= date_to + timedelta(days=1),
    )
    if portfolio_id:
        stmt = stmt.where(Transaction.portfolio_id.in_(portfolio_id))
    rows = (await db.execute(stmt)).all()
    keys: set[tuple[int, date]] = set()
    for asset_id, executed_at in rows:
        if asset_id is None:
            continue
        # выплата могла быть зачислена на пару дней позже плановой даты
        for shift in range(-3, 4):
            keys.add((asset_id, executed_at.date() + timedelta(days=shift)))
    return keys
