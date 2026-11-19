from fastapi import APIRouter, Query

from app.services.macro import macro_snapshot

router = APIRouter(prefix="/macro", tags=["Макроэкономика"])


@router.get("", summary="Ключевая ставка, инфляция и доходность ОФЗ")
async def macro(force: bool = Query(False, description="Обновить кеш источников")):
    return await macro_snapshot(force=force)
