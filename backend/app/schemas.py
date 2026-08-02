from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import AssetClass, PayoutFrequency, PortfolioKind, TxType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ портфели


class PortfolioBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: PortfolioKind = PortfolioKind.BROKER
    base_currency: str = "RUB"
    broker: str | None = None
    opened_at: date | None = None
    note: str | None = None


class PortfolioCreate(PortfolioBase):
    pass


class PortfolioUpdate(BaseModel):
    name: str | None = None
    kind: PortfolioKind | None = None
    base_currency: str | None = None
    broker: str | None = None
    opened_at: date | None = None
    note: str | None = None
    is_archived: bool | None = None


class PortfolioOut(ORMModel, PortfolioBase):
    id: int
    is_archived: bool


class PortfolioWithValue(PortfolioOut):
    total_value: float
    total_pnl: float
    total_pnl_pct: float | None
    day_change: float
    positions_count: int


# -------------------------------------------------------------------- активы


class AssetOut(ORMModel):
    id: int
    secid: str
    name: str
    short_name: str | None
    asset_class: str
    currency: str
    engine: str | None
    market: str | None
    board: str | None
    isin: str | None
    lot_size: int
    sector: str | None
    face_value: float | None
    initial_face_value: float | None
    coupon_rate: float | None
    coupon_period: int | None
    maturity_date: date | None
    offer_date: date | None
    is_custom: bool
    annual_rate: float | None
    payout_frequency: str | None
    manual_price: float | None
    capitalization: bool
    start_date: date | None
    end_date: date | None
    is_active: bool


class CustomAssetCreate(BaseModel):
    """Кастомный актив: вклад, накопительный счёт, что угодно вне биржи."""

    secid: str = Field(min_length=1, max_length=64, description="Уникальный код, например DEPOSIT-SBER")
    name: str = Field(min_length=1, max_length=256)
    asset_class: Literal[AssetClass.DEPOSIT, AssetClass.CUSTOM] = AssetClass.DEPOSIT
    currency: str = "RUB"
    manual_price: float = Field(default=1.0, gt=0, description="Цена единицы; для вклада оставьте 1")
    annual_rate: float | None = Field(default=None, description="Ставка, % годовых")
    payout_frequency: PayoutFrequency = PayoutFrequency.MONTHLY
    capitalization: bool = False
    start_date: date | None = None
    end_date: date | None = None
    note: str | None = None

    @field_validator("secid")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.strip().upper()


class AssetUpdate(BaseModel):
    name: str | None = None
    short_name: str | None = None
    sector: str | None = None
    manual_price: float | None = None
    annual_rate: float | None = None
    payout_frequency: PayoutFrequency | None = None
    capitalization: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    face_value: float | None = None
    coupon_rate: float | None = None
    maturity_date: date | None = None
    is_active: bool | None = None


class AssetSearchResult(BaseModel):
    secid: str
    name: str | None
    short_name: str | None
    isin: str | None
    asset_class: str
    board: str | None
    in_portfolio: bool = False


# ------------------------------------------------------------------- сделки


class TransactionBase(BaseModel):
    portfolio_id: int
    type: TxType
    executed_at: datetime
    secid: str | None = Field(default=None, description="Тикер; не нужен для DEPOSIT/WITHDRAWAL")
    price: float = 0
    quantity: float = 0
    amount: float | None = Field(default=None, description="Если не задано — price × quantity")
    fee: float = 0
    tax: float = 0
    accrued_interest: float = Field(default=0, description="НКД")
    currency: str = "RUB"
    note: str | None = None
    do_not_adjust_cash: bool = False

    @field_validator("secid")
    @classmethod
    def upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None


class TransactionCreate(TransactionBase):
    pass


class TransactionUpdate(BaseModel):
    type: TxType | None = None
    executed_at: datetime | None = None
    price: float | None = None
    quantity: float | None = None
    amount: float | None = None
    fee: float | None = None
    tax: float | None = None
    accrued_interest: float | None = None
    note: str | None = None
    do_not_adjust_cash: bool | None = None


class TransactionOut(ORMModel):
    id: int
    portfolio_id: int
    portfolio_name: str | None = None
    asset_id: int | None
    secid: str | None = None
    asset_name: str | None = None
    asset_class: str | None = None
    type: str
    executed_at: datetime
    price: float
    quantity: float
    amount: float
    fee: float
    tax: float
    accrued_interest: float
    currency: str
    note: str | None
    source: str
    do_not_adjust_cash: bool


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------- импорт


class ImportPreviewRow(BaseModel):
    line: int
    event: str
    type: str
    executed_at: str
    symbol: str | None
    price: float
    quantity: float
    amount: float
    fee: float
    accrued_interest: float
    currency: str
    note: str | None
    warnings: list[str]
    amount_rule: str | None
    external_id: str
    is_duplicate: bool = False


class ImportPreview(BaseModel):
    filename: str
    total_lines: int
    parsed: int
    errors: list[dict[str, Any]]
    duplicates: int
    new_symbols: list[str]
    known_symbols: list[str]
    rows: list[ImportPreviewRow]
    summary: dict[str, int]


class ImportCommitResult(BaseModel):
    batch_id: int
    imported: int
    skipped: int
    failed: int
    created_assets: list[str]
    unresolved_assets: list[str]
    errors: list[dict[str, Any]]


class ImportBatchOut(ORMModel):
    id: int
    portfolio_id: int | None
    filename: str
    format: str
    status: str
    total_rows: int
    imported: int
    skipped: int
    failed: int
    created_at: datetime


# ---------------------------------------------------------------- календарь


class CalendarItem(BaseModel):
    asset_id: int
    secid: str
    name: str
    asset_class: str
    kind: str
    payment_date: date
    record_date: date | None
    value_per_unit: float
    quantity: float
    amount: float
    currency: str
    is_forecast: bool
    is_paid: bool = False
    note: str | None = None


class CalendarMonth(BaseModel):
    month: str
    amount: float
    count: int


class CalendarResponse(BaseModel):
    date_from: date
    date_to: date
    items: list[CalendarItem]
    by_month: list[CalendarMonth]
    total_amount: float
    next_payment: CalendarItem | None = None


# ------------------------------------------------------------------ прочее


class SyncRequest(BaseModel):
    secids: list[str] | None = None
    with_history: bool = True
    with_schedule: bool = True
    with_benchmarks: bool = True


class SyncResult(BaseModel):
    assets_updated: int
    prices_loaded: int
    payments_loaded: int
    benchmarks_updated: int
    quotes_updated: int
    errors: list[str]


class HealthOut(BaseModel):
    status: str
    database: bool
    quote_provider: str
    quote_delayed: bool
    ws_clients: int
    assets: int
    transactions: int
