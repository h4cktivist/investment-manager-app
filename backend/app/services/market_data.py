"""Синхронизация справочников, истории цен и календарей выплат с MOEX ISS."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AssetClass, PaymentKind
from app.models import Asset, Benchmark, BenchmarkPrice, PaymentSchedule, PriceHistory, Transaction
from app.services.moex import MoexISS, normalize_price

log = logging.getLogger(__name__)

DEFAULT_BENCHMARKS = [
    ("IMOEX", "Индекс МосБиржи (ценовой)", "EQUITY", True),
    ("MCFTR", "Индекс МосБиржи полной доходности «брутто»", "EQUITY", False),
    ("RGBITR", "Индекс гособлигаций РФ, полной доходности", "BOND_GOV", False),
    ("RUCBTRNS", "Индекс корпоративных облигаций, полной доходности", "BOND_CORP", False),
    ("RUGBITR10Y", "Индекс ОФЗ 10 лет, полной доходности", "BOND_GOV", False),
]

HISTORY_START = date(2013, 1, 1)
#: сколько дней истории брать до первой сделки по бумаге
DAYS_BEFORE_FIRST_TRADE = 30


def _dec(value: float | int | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def ensure_benchmarks(session: AsyncSession) -> None:
    existing = {b.code for b in (await session.scalars(select(Benchmark))).all()}
    for code, name, kind, is_default in DEFAULT_BENCHMARKS:
        if code in existing:
            continue
        session.add(
            Benchmark(code=code, name=name, kind=kind, is_default=is_default)
        )
    await session.flush()


# --------------------------------------------------------------------- активы


async def get_or_create_asset(
    session: AsyncSession, secid: str, iss: MoexISS | None = None
) -> Asset | None:
    """Находит инструмент в БД, при отсутствии — заводит по данным ISS."""
    secid = secid.strip().upper()
    asset = await session.scalar(select(Asset).where(Asset.secid == secid))
    if asset is not None:
        return asset

    info = None
    if iss is not None:
        try:
            info = await iss.security_info(secid)
        except Exception as exc:  # noqa: BLE001 — ISS может быть недоступен
            log.warning("ISS security_info(%s) failed: %s", secid, exc)

    if info is None:
        # инструмент неизвестен бирже — заводим заглушку, пользователь дополнит руками
        asset = Asset(
            secid=secid,
            name=secid,
            short_name=secid,
            asset_class=AssetClass.CUSTOM.value,
            is_custom=True,
            meta={"unresolved": True},
        )
        session.add(asset)
        await session.flush()
        return asset

    asset = Asset(
        secid=secid,
        name=info["name"],
        short_name=info.get("short_name"),
        asset_class=info["asset_class"],
        currency=info.get("currency") or "RUB",
        engine=info.get("engine"),
        market=info.get("market"),
        board=info.get("board"),
        isin=info.get("isin"),
        lot_size=info.get("lot_size") or 1,
        face_value=_dec(info.get("face_value") or info.get("initial_face_value")),
        initial_face_value=_dec(info.get("initial_face_value")),
        coupon_rate=_dec(info.get("coupon_rate")),
        coupon_period=info.get("coupon_period"),
        maturity_date=info.get("maturity_date"),
        offer_date=info.get("offer_date"),
        is_active=bool(info.get("is_traded", True)),
        meta={"iss_type": info.get("type"), "iss_group": info.get("group")},
    )
    session.add(asset)
    await session.flush()
    return asset


async def refresh_asset_reference(session: AsyncSession, asset: Asset, iss: MoexISS) -> None:
    """Обновляет справочные поля инструмента (номинал, дата погашения и т.п.)."""
    if asset.is_custom:
        return
    try:
        info = await iss.security_info(asset.secid)
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_asset_reference(%s): %s", asset.secid, exc)
        return
    if not info:
        return
    asset.name = info["name"] or asset.name
    asset.short_name = info.get("short_name") or asset.short_name
    asset.asset_class = info["asset_class"]
    asset.currency = info.get("currency") or asset.currency
    asset.engine = info.get("engine") or asset.engine
    asset.market = info.get("market") or asset.market
    asset.board = info.get("board") or asset.board
    asset.isin = info.get("isin") or asset.isin
    asset.lot_size = info.get("lot_size") or asset.lot_size
    asset.initial_face_value = _dec(info.get("initial_face_value")) or asset.initial_face_value
    asset.face_value = _dec(info.get("face_value")) or asset.face_value or asset.initial_face_value
    asset.coupon_rate = _dec(info.get("coupon_rate")) or asset.coupon_rate
    asset.coupon_period = info.get("coupon_period") or asset.coupon_period
    asset.maturity_date = info.get("maturity_date") or asset.maturity_date
    asset.offer_date = info.get("offer_date") or asset.offer_date
    asset.is_active = bool(info.get("is_traded", True))


# ---------------------------------------------------------------- история цен


async def sync_price_history(
    session: AsyncSession,
    asset: Asset,
    start: date | None = None,
    end: date | None = None,
    iss: MoexISS | None = None,
) -> int:
    """Догружает дневные цены. Возвращает количество новых/обновлённых точек."""
    if asset.is_custom or not asset.engine or not asset.market:
        return 0
    if iss is None:
        raise ValueError("нужен экземпляр MoexISS")

    end = end or date.today()
    if start is None:
        last = await session.scalar(
            select(PriceHistory.date)
            .where(PriceHistory.asset_id == asset.id)
            .order_by(PriceHistory.date.desc())
            .limit(1)
        )
        start = (last + timedelta(days=1)) if last else await _history_start_for(session, asset)
    if start > end:
        return 0

    # доску указываем обязательно: без неё ISS отдаёт историю по всем режимам
    # торгов сразу, и на одну дату приходится несколько строк
    rows = await iss.history(
        asset.engine, asset.market, asset.secid, start, end, board=asset.board
    )
    if not rows and asset.board:
        rows = await iss.history(asset.engine, asset.market, asset.secid, start, end)
    if not rows:
        return 0

    fallback_face = float(asset.face_value or asset.initial_face_value or 0) or None
    # на всякий случай схлопываем возможные дубли дат
    by_date: dict[date, dict] = {}
    for row in rows:
        face = row.get("face_value") or fallback_face
        by_date[row["date"]] = {
            "asset_id": asset.id,
            "date": row["date"],
            "close": _dec(row["close"]),
            "close_norm": _dec(normalize_price(asset.asset_class, row["close"], face)),
            "accrued_interest": _dec(row.get("accrued_interest")),
            "volume": _dec(row.get("volume")),
        }

    payload = [by_date[key] for key in sorted(by_date)]
    for chunk in _chunks(payload, 2000):
        stmt = pg_insert(PriceHistory).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_price_asset_date",
            set_={
                "close": stmt.excluded.close,
                "close_norm": stmt.excluded.close_norm,
                "accrued_interest": stmt.excluded.accrued_interest,
                "volume": stmt.excluded.volume,
            },
        )
        await session.execute(stmt)
    return len(payload)


def _chunks(items: list, size: int):
    """asyncpg не принимает больше 32767 параметров в одном запросе."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _history_start_for(session: AsyncSession, asset: Asset) -> date:
    """История нужна с первой сделки по бумаге, а не с сотворения биржи.

    Тянуть по 10+ лет котировок на каждый инструмент бессмысленно и приводит
    к тому, что ISS начинает обрывать соединения.
    """
    first_tx = await session.scalar(
        select(Transaction.executed_at)
        .where(Transaction.asset_id == asset.id)
        .order_by(Transaction.executed_at)
        .limit(1)
    )
    if first_tx is not None:
        return first_tx.date() - timedelta(days=DAYS_BEFORE_FIRST_TRADE)
    return max(date.today() - timedelta(days=365 * 2), HISTORY_START)


async def sync_benchmark_history(
    session: AsyncSession, benchmark: Benchmark, iss: MoexISS, start: date | None = None
) -> int:
    if start is None:
        last = await session.scalar(
            select(BenchmarkPrice.date)
            .where(BenchmarkPrice.benchmark_id == benchmark.id)
            .order_by(BenchmarkPrice.date.desc())
            .limit(1)
        )
        start = (last + timedelta(days=1)) if last else HISTORY_START
    end = date.today()
    if start > end:
        return 0

    rows = await iss.index_history(benchmark.code, start, end)
    if not rows:
        return 0
    by_date = {r["date"]: _dec(r["close"]) for r in rows}
    payload = [
        {"benchmark_id": benchmark.id, "date": day, "close": by_date[day]}
        for day in sorted(by_date)
    ]
    for chunk in _chunks(payload, 5000):
        stmt = pg_insert(BenchmarkPrice).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_benchmark_price_date", set_={"close": stmt.excluded.close}
        )
        await session.execute(stmt)
    return len(payload)


# ------------------------------------------------------------ календарь выплат


async def sync_payment_schedule(session: AsyncSession, asset: Asset, iss: MoexISS) -> int:
    """Тянет купоны/амортизации по облигациям и историю дивидендов по акциям."""
    if asset.is_custom:
        return 0

    records: list[dict] = []

    if asset.asset_class == AssetClass.BOND.value:
        try:
            data = await iss.bondization(asset.secid)
        except Exception as exc:  # noqa: BLE001
            log.warning("bondization(%s): %s", asset.secid, exc)
            return 0

        coupons = data["coupons"]
        # у флоатеров будущие купоны неизвестны — прогнозируем последним известным
        known = [c["value"] for c in coupons if c["value"] is not None]
        forecast_value = known[-1] if known else None
        for c in coupons:
            value = c["value"]
            is_forecast = value is None
            if is_forecast:
                value = forecast_value
            if value is None:
                continue
            records.append(
                {
                    "asset_id": asset.id,
                    "kind": PaymentKind.COUPON.value,
                    "payment_date": c["date"],
                    "record_date": c.get("record_date"),
                    "value_per_unit": _dec(value),
                    "currency": c.get("currency") or asset.currency,
                    "is_forecast": is_forecast,
                    "source": "moex",
                    "note": "прогноз по последнему известному купону" if is_forecast else None,
                }
            )

        for a in data["amortizations"]:
            if not a.get("value"):
                continue
            is_maturity = asset.maturity_date and a["date"] == asset.maturity_date
            records.append(
                {
                    "asset_id": asset.id,
                    "kind": (
                        PaymentKind.MATURITY.value if is_maturity else PaymentKind.AMORTIZATION.value
                    ),
                    "payment_date": a["date"],
                    "record_date": None,
                    "value_per_unit": _dec(a["value"]),
                    "currency": a.get("currency") or asset.currency,
                    "is_forecast": False,
                    "source": "moex",
                    "note": None,
                }
            )

        for o in data["offers"]:
            records.append(
                {
                    "asset_id": asset.id,
                    "kind": PaymentKind.OFFER.value,
                    "payment_date": o["date"],
                    "record_date": None,
                    "value_per_unit": _dec(o.get("price") or 0),
                    "currency": asset.currency,
                    "is_forecast": False,
                    "source": "moex",
                    "note": o.get("type"),
                }
            )

    elif asset.asset_class in (AssetClass.SHARE.value, AssetClass.ETF.value):
        try:
            divs = await iss.dividends(asset.secid)
        except Exception as exc:  # noqa: BLE001
            log.warning("dividends(%s): %s", asset.secid, exc)
            return 0
        for d in divs:
            # ISS отдаёт дату закрытия реестра; деньги приходят обычно через 8–18 дней
            records.append(
                {
                    "asset_id": asset.id,
                    "kind": PaymentKind.DIVIDEND.value,
                    "payment_date": d["record_date"] + timedelta(days=10),
                    "record_date": d["record_date"],
                    "value_per_unit": _dec(d["value"]),
                    "currency": d.get("currency") or asset.currency,
                    "is_forecast": d["record_date"] > date.today(),
                    "source": "moex",
                    "note": "дата выплаты оценочная (T+10 от отсечки)",
                }
            )

    if not records:
        return 0

    # дубли по (asset, kind, date) схлопываем — ISS иногда повторяет строки
    unique: dict[tuple, dict] = {}
    for r in records:
        unique[(r["asset_id"], r["kind"], r["payment_date"])] = r

    stmt = pg_insert(PaymentSchedule).values(list(unique.values()))
    stmt = stmt.on_conflict_do_update(
        constraint="uq_payment_asset_kind_date",
        set_={
            "value_per_unit": stmt.excluded.value_per_unit,
            "record_date": stmt.excluded.record_date,
            "is_forecast": stmt.excluded.is_forecast,
            "note": stmt.excluded.note,
        },
    )
    await session.execute(stmt)
    return len(unique)


async def face_value_at(session: AsyncSession, asset: Asset, when: date) -> float:
    """Номинал облигации на дату с учётом уже прошедших амортизаций."""
    initial = float(asset.initial_face_value or asset.face_value or 0)
    if not initial:
        return 0.0
    rows = await session.scalars(
        select(PaymentSchedule.value_per_unit).where(
            PaymentSchedule.asset_id == asset.id,
            PaymentSchedule.kind == PaymentKind.AMORTIZATION.value,
            PaymentSchedule.payment_date <= when,
        )
    )
    repaid = sum(float(v) for v in rows.all())
    return max(initial - repaid, 0.0)
