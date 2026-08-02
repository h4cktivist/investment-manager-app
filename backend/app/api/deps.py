from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(get_db)]


async def portfolio_filter(
    portfolio_id: Annotated[
        list[int] | None,
        Query(description="ID портфелей; можно повторять. Пусто — все портфели"),
    ] = None,
) -> list[int] | None:
    return portfolio_id or None


PortfolioIds = Annotated[list[int] | None, Depends(portfolio_filter)]


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} не найден")
