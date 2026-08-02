from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, func, select

from app.api.deps import DB, not_found
from app.models import Portfolio, Transaction
from app.schemas import PortfolioCreate, PortfolioOut, PortfolioUpdate, PortfolioWithValue
from app.services.analytics import portfolio_list_with_values

router = APIRouter(prefix="/portfolios", tags=["Портфели"])


@router.get("", response_model=list[PortfolioWithValue], summary="Список портфелей с оценкой")
async def list_portfolios(db: DB):
    return await portfolio_list_with_values(db)


@router.post("", response_model=PortfolioOut, status_code=201, summary="Создать портфель")
async def create_portfolio(payload: PortfolioCreate, db: DB):
    exists = await db.scalar(select(Portfolio).where(Portfolio.name == payload.name))
    if exists:
        raise HTTPException(409, f"Портфель «{payload.name}» уже существует")
    portfolio = Portfolio(**payload.model_dump())
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@router.get("/{portfolio_id}", response_model=PortfolioOut, summary="Портфель")
async def get_portfolio(portfolio_id: int, db: DB):
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")
    return portfolio


@router.patch("/{portfolio_id}", response_model=PortfolioOut, summary="Изменить портфель")
async def update_portfolio(portfolio_id: int, payload: PortfolioUpdate, db: DB):
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(portfolio, key, value)
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@router.delete("/{portfolio_id}", status_code=204, summary="Удалить портфель со сделками")
async def delete_portfolio(portfolio_id: int, db: DB):
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")
    await db.execute(delete(Transaction).where(Transaction.portfolio_id == portfolio_id))
    await db.delete(portfolio)
    await db.commit()


@router.get("/{portfolio_id}/stats", summary="Счётчики портфеля")
async def portfolio_stats(portfolio_id: int, db: DB):
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise not_found("Портфель")
    total = await db.scalar(
        select(func.count(Transaction.id)).where(Transaction.portfolio_id == portfolio_id)
    )
    first = await db.scalar(
        select(func.min(Transaction.executed_at)).where(Transaction.portfolio_id == portfolio_id)
    )
    last = await db.scalar(
        select(func.max(Transaction.executed_at)).where(Transaction.portfolio_id == portfolio_id)
    )
    return {
        "transactions": total or 0,
        "first_transaction": first.isoformat() if first else None,
        "last_transaction": last.isoformat() if last else None,
    }
