import logging
from collections import Counter

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.api.deps import DB, not_found
from app.db import SessionLocal
from app.enums import AssetClass, ImportStatus, TxType
from app.models import Asset, ImportBatch, Portfolio, Transaction
from app.schemas import (
    ImportBatchOut,
    ImportCommitResult,
    ImportPreview,
    ImportPreviewRow,
)
from app.services.importers.broker_csv import ImportError_, parse_csv
from app.services.market_data import get_or_create_asset, sync_payment_schedule, sync_price_history
from app.services.moex import MoexISS
from app.services.payments import find_auto_payment
from app.services.realtime import refresh_quotes_once

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["Импорт"])

MAX_SIZE = 20 * 1024 * 1024
#: код формата, под которым сохраняются партии импорта
CSV_FORMAT = "broker_csv"

#: выплаты, которые могла успеть создать автоматическая задача
PAYMENT_TYPES = {
    TxType.DIVIDEND.value,
    TxType.COUPON.value,
    TxType.AMORTIZATION.value,
    TxType.INTEREST.value,
}


def _filename(file: UploadFile) -> str:
    """Заголовок multipart читается как latin-1 — чиним кириллицу в имени файла."""
    raw = file.filename or "import.csv"
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


async def _read(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(413, "Файл больше 20 МБ")
    if not content:
        raise HTTPException(400, "Файл пустой")
    return content


@router.post("/preview", response_model=ImportPreview, summary="Разобрать файл без записи в базу")
async def preview_import(db: DB, file: UploadFile = File(...)):
    content = await _read(file)
    try:
        result = parse_csv(content)
    except ImportError_ as exc:
        raise HTTPException(400, str(exc)) from exc

    known_secids = set(
        (await db.scalars(
            select(Asset.secid).where(Asset.secid.in_(result.symbols))
        )).all()
    )
    existing_ids = set(
        (
            await db.scalars(
                select(Transaction.external_id).where(
                    Transaction.external_id.in_([r.external_id for r in result.rows])
                )
            )
        ).all()
    )

    rows = []
    duplicates = 0
    for row in result.rows:
        is_dup = row.external_id in existing_ids
        duplicates += int(is_dup)
        rows.append(ImportPreviewRow(**row.to_dict(), is_duplicate=is_dup))

    return ImportPreview(
        filename=_filename(file),
        total_lines=result.total_lines,
        parsed=result.ok,
        errors=result.errors,
        duplicates=duplicates,
        new_symbols=sorted(set(result.symbols) - known_secids),
        known_symbols=sorted(known_secids),
        rows=rows,
        summary=dict(Counter(r.type for r in result.rows)),
    )


@router.post("", response_model=ImportCommitResult, summary="Импортировать сделки в портфель")
async def commit_import(
    db: DB,
    background: BackgroundTasks,
    portfolio_id: int = Form(...),
    skip_duplicates: bool = Form(True),
    load_market_data: bool = Form(True),
    file: UploadFile = File(...),
):
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")

    content = await _read(file)
    try:
        result = parse_csv(content)
    except ImportError_ as exc:
        raise HTTPException(400, str(exc)) from exc

    batch = ImportBatch(
        portfolio_id=portfolio_id,
        filename=_filename(file),
        format=CSV_FORMAT,
        status=ImportStatus.PENDING.value,
        total_rows=result.total_lines,
    )
    db.add(batch)
    await db.flush()

    existing_ids = set(
        (
            await db.scalars(
                select(Transaction.external_id).where(
                    Transaction.external_id.in_([r.external_id for r in result.rows]),
                    Transaction.portfolio_id == portfolio_id,
                )
            )
        ).all()
    )

    created_assets: list[str] = []
    unresolved: list[str] = []
    asset_cache: dict[str, Asset] = {}
    errors = list(result.errors)
    imported = skipped = failed = 0

    async with MoexISS() as iss:
        for symbol in result.symbols:
            existing = await db.scalar(select(Asset).where(Asset.secid == symbol))
            if existing is not None:
                asset_cache[symbol] = existing
                continue
            asset = await get_or_create_asset(db, symbol, iss)
            if asset is None:
                unresolved.append(symbol)
                continue
            asset_cache[symbol] = asset
            created_assets.append(symbol)
            if asset.is_custom and (asset.meta or {}).get("unresolved"):
                unresolved.append(symbol)

    for row in result.rows:
        if skip_duplicates and row.external_id in existing_ids:
            skipped += 1
            continue
        asset = asset_cache.get(row.symbol) if row.symbol else None
        if row.symbol and asset is None:
            failed += 1
            errors.append({"line": row.line, "error": f"не удалось определить актив {row.symbol}"})
            continue
        # выгрузки помечают купоны облигаций тем же событием DIVIDEND —
        # класс инструмента известен только после его разрешения в ISS
        tx_type = row.type
        if (
            tx_type == TxType.DIVIDEND.value
            and asset is not None
            and asset.asset_class == AssetClass.BOND.value
        ):
            tx_type = TxType.COUPON.value

        # Автозадача могла уже создать операцию по этой выплате. Не плодим
        # дубль: заменяем автоматическую запись фактическими данными отчёта.
        if (
            asset is not None
            and tx_type in PAYMENT_TYPES
            and row.external_id not in existing_ids
        ):
            auto_tx = await find_auto_payment(
                db, portfolio_id, asset.id, tx_type, row.executed_at.date()
            )
            if auto_tx is not None:
                auto_tx.type = tx_type
                auto_tx.executed_at = row.executed_at
                auto_tx.price = row.price
                auto_tx.quantity = row.quantity
                auto_tx.amount = row.amount
                auto_tx.fee = row.fee
                auto_tx.tax = 0
                auto_tx.accrued_interest = row.accrued_interest
                auto_tx.currency = row.currency
                auto_tx.note = row.note or auto_tx.note
                auto_tx.source = "import"
                auto_tx.external_id = row.external_id
                auto_tx.import_batch_id = batch.id
                auto_tx.do_not_adjust_cash = row.do_not_adjust_cash
                imported += 1
                existing_ids.add(row.external_id)
                continue

        try:
            db.add(
                Transaction(
                    portfolio_id=portfolio_id,
                    asset_id=asset.id if asset else None,
                    type=tx_type,
                    executed_at=row.executed_at,
                    price=row.price,
                    quantity=row.quantity,
                    amount=row.amount,
                    fee=row.fee,
                    tax=0,
                    accrued_interest=row.accrued_interest,
                    currency=row.currency,
                    note=row.note or (row.amount_rule if row.warnings else None),
                    source="import",
                    external_id=row.external_id,
                    import_batch_id=batch.id,
                    do_not_adjust_cash=row.do_not_adjust_cash,
                )
            )
            imported += 1
            existing_ids.add(row.external_id)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"line": row.line, "error": str(exc)})

    batch.imported = imported
    batch.skipped = skipped
    batch.failed = failed
    batch.status = ImportStatus.DONE.value if not failed else ImportStatus.FAILED.value
    batch.report = {
        "created_assets": created_assets,
        "unresolved_assets": unresolved,
        "errors": errors[:100],
    }
    await db.commit()

    if load_market_data:
        secids = list(asset_cache)
        background.add_task(_load_market_data, secids)

    return ImportCommitResult(
        batch_id=batch.id,
        imported=imported,
        skipped=skipped,
        failed=failed,
        created_assets=created_assets,
        unresolved_assets=sorted(set(unresolved)),
        errors=errors[:100],
    )


async def _load_market_data(secids: list[str]) -> None:
    """Фоновая догрузка истории и календарей после импорта."""
    log.info("подгружаю рыночные данные для %s инструментов", len(secids))
    async with SessionLocal() as session, MoexISS() as iss:
        for secid in secids:
            asset = await session.scalar(select(Asset).where(Asset.secid == secid))
            if asset is None or asset.is_custom:
                continue
            try:
                await sync_price_history(session, asset, iss=iss)
                await sync_payment_schedule(session, asset, iss)
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                log.warning("не удалось загрузить данные по %s: %s", secid, exc)
    try:
        await refresh_quotes_once()
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось обновить котировки: %s", exc)
    log.info("рыночные данные загружены")


@router.get("/batches", response_model=list[ImportBatchOut], summary="История импортов")
async def list_batches(db: DB, limit: int = 50):
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit)
    return list((await db.scalars(stmt)).all())


@router.get("/batches/{batch_id}", summary="Отчёт по импорту")
async def get_batch(batch_id: int, db: DB):
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise not_found("Импорт")
    return {
        "id": batch.id,
        "portfolio_id": batch.portfolio_id,
        "filename": batch.filename,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "imported": batch.imported,
        "skipped": batch.skipped,
        "failed": batch.failed,
        "created_at": batch.created_at.isoformat(),
        "report": batch.report,
    }


@router.delete("/batches/{batch_id}", summary="Откатить импорт вместе со сделками")
async def rollback_batch(batch_id: int, db: DB):
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise not_found("Импорт")
    rows = list(
        (await db.scalars(select(Transaction).where(Transaction.import_batch_id == batch_id))).all()
    )
    for tx in rows:
        await db.delete(tx)
    await db.delete(batch)
    await db.commit()
    return {"deleted_transactions": len(rows)}
