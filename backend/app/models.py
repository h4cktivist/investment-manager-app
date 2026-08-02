from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MONEY = Numeric(24, 8)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="BROKER")
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    broker: Mapped[str | None] = mapped_column(String(128))
    opened_at: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", passive_deletes=True
    )


class Asset(TimestampMixin, Base):
    """Инструмент. Биржевые тянутся из MOEX ISS, кастомные заводятся руками."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    secid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(128))
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, default="SHARE")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")

    # координаты инструмента в ISS
    engine: Mapped[str | None] = mapped_column(String(32))
    market: Mapped[str | None] = mapped_column(String(32))
    board: Mapped[str | None] = mapped_column(String(32))
    isin: Mapped[str | None] = mapped_column(String(32))
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sector: Mapped[str | None] = mapped_column(String(128))

    # облигационные атрибуты
    face_value: Mapped[Decimal | None] = mapped_column(MONEY)
    initial_face_value: Mapped[Decimal | None] = mapped_column(MONEY)
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))  # % годовых
    coupon_period: Mapped[int | None] = mapped_column(Integer)          # дней
    maturity_date: Mapped[date | None] = mapped_column(Date)
    offer_date: Mapped[date | None] = mapped_column(Date)

    # кастомные активы (вклад, накопительный счёт, недвижимость и т.п.)
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    annual_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))  # % годовых
    payout_frequency: Mapped[str | None] = mapped_column(String(16))
    manual_price: Mapped[Decimal | None] = mapped_column(MONEY)
    capitalization: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict | None] = mapped_column(JSONB)

    prices: Mapped[list[PriceHistory]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", passive_deletes=True
    )
    quote: Mapped[Quote | None] = relationship(
        back_populates="asset", cascade="all, delete-orphan", uselist=False, passive_deletes=True
    )
    payments: Mapped[list[PaymentSchedule]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_bond(self) -> bool:
        return self.asset_class == "BOND"


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    #: итоговая сумма события в валюте сделки (без комиссии/налога)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    accrued_interest: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)  # НКД

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    fx_rate: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=1)

    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(128))
    import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    do_not_adjust_cash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    portfolio: Mapped[Portfolio] = relationship(back_populates="transactions")
    asset: Mapped[Asset | None] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_tx_portfolio_time", "portfolio_id", "executed_at"),
        Index("ix_tx_asset_time", "asset_id", "executed_at"),
        UniqueConstraint("portfolio_id", "external_id", name="uq_tx_portfolio_external"),
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: котировка «как есть» (для облигаций — % от номинала)
    close: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    #: цена за одну бумагу в валюте инструмента (для облигаций уже в рублях)
    close_norm: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    accrued_interest: Mapped[Decimal | None] = mapped_column(MONEY)
    volume: Mapped[Decimal | None] = mapped_column(MONEY)

    asset: Mapped[Asset] = relationship(back_populates="prices")

    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_price_asset_date"),)


class Quote(Base):
    """Последняя известная котировка (realtime-кэш, переживает рестарт)."""

    __tablename__ = "quotes"

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price_norm: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    prev_close: Mapped[Decimal | None] = mapped_column(MONEY)
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    accrued_interest: Mapped[Decimal | None] = mapped_column(MONEY)
    bid: Mapped[Decimal | None] = mapped_column(MONEY)
    ask: Mapped[Decimal | None] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="moex")
    is_delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    asset: Mapped[Asset] = relationship(back_populates="quote")


class PaymentSchedule(Base):
    """Календарь выплат: купоны, амортизации, дивиденды, доход по кастомным активам."""

    __tablename__ = "payment_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    record_date: Mapped[date | None] = mapped_column(Date)
    #: выплата на одну бумагу
    value_per_unit: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    is_forecast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="moex")
    note: Mapped[str | None] = mapped_column(Text)

    asset: Mapped[Asset] = relationship(back_populates="payments")

    __table_args__ = (
        UniqueConstraint("asset_id", "kind", "payment_date", name="uq_payment_asset_kind_date"),
    )


class Benchmark(Base):
    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="EQUITY")
    engine: Mapped[str] = mapped_column(String(32), nullable=False, default="stock")
    market: Mapped[str] = mapped_column(String(32), nullable=False, default="index")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    prices: Mapped[list[BenchmarkPrice]] = relationship(
        back_populates="benchmark", cascade="all, delete-orphan", passive_deletes=True
    )


class BenchmarkPrice(Base):
    __tablename__ = "benchmark_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    benchmark_id: Mapped[int] = mapped_column(
        ForeignKey("benchmarks.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    close: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    benchmark: Mapped[Benchmark] = relationship(back_populates="prices")

    __table_args__ = (
        UniqueConstraint("benchmark_id", "date", name="uq_benchmark_price_date"),
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="broker_csv")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
