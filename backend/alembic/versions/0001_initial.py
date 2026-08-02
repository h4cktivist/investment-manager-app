"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(24, 8)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="BROKER"),
        sa.Column("base_currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("broker", sa.String(128)),
        sa.Column("opened_at", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("secid", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("short_name", sa.String(128)),
        sa.Column("asset_class", sa.String(16), nullable=False, server_default="SHARE"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("engine", sa.String(32)),
        sa.Column("market", sa.String(32)),
        sa.Column("board", sa.String(32)),
        sa.Column("isin", sa.String(32)),
        sa.Column("lot_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sector", sa.String(128)),
        sa.Column("face_value", MONEY),
        sa.Column("initial_face_value", MONEY),
        sa.Column("coupon_rate", sa.Numeric(12, 6)),
        sa.Column("coupon_period", sa.Integer()),
        sa.Column("maturity_date", sa.Date()),
        sa.Column("offer_date", sa.Date()),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("annual_rate", sa.Numeric(12, 6)),
        sa.Column("payout_frequency", sa.String(16)),
        sa.Column("manual_price", MONEY),
        sa.Column("capitalization", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("meta", postgresql.JSONB()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_assets_secid", "assets", ["secid"])

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
        ),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("format", sa.String(32), nullable=False, server_default="broker_csv"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", postgresql.JSONB()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.Integer(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="RESTRICT")),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("price", MONEY, nullable=False, server_default="0"),
        sa.Column("quantity", MONEY, nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("fee", MONEY, nullable=False, server_default="0"),
        sa.Column("tax", MONEY, nullable=False, server_default="0"),
        sa.Column("accrued_interest", MONEY, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("fx_rate", MONEY, nullable=False, server_default="1"),
        sa.Column("note", sa.Text()),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.String(128)),
        sa.Column(
            "import_batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("do_not_adjust_cash", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("portfolio_id", "external_id", name="uq_tx_portfolio_external"),
    )
    op.create_index("ix_transactions_portfolio_id", "transactions", ["portfolio_id"])
    op.create_index("ix_transactions_asset_id", "transactions", ["asset_id"])
    op.create_index("ix_tx_portfolio_time", "transactions", ["portfolio_id", "executed_at"])
    op.create_index("ix_tx_asset_time", "transactions", ["asset_id", "executed_at"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("close", MONEY, nullable=False),
        sa.Column("close_norm", MONEY, nullable=False),
        sa.Column("accrued_interest", MONEY),
        sa.Column("volume", MONEY),
        sa.UniqueConstraint("asset_id", "date", name="uq_price_asset_date"),
    )

    op.create_table(
        "quotes",
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("price_norm", MONEY, nullable=False),
        sa.Column("prev_close", MONEY),
        sa.Column("change_pct", sa.Numeric(12, 6)),
        sa.Column("accrued_interest", MONEY),
        sa.Column("bid", MONEY),
        sa.Column("ask", MONEY),
        sa.Column("source", sa.String(16), nullable=False, server_default="moex"),
        sa.Column("is_delayed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "payment_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("record_date", sa.Date()),
        sa.Column("value_per_unit", MONEY, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("is_forecast", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(24), nullable=False, server_default="moex"),
        sa.Column("note", sa.Text()),
        sa.UniqueConstraint("asset_id", "kind", "payment_date", name="uq_payment_asset_kind_date"),
    )
    op.create_index("ix_payment_schedule_asset_id", "payment_schedule", ["asset_id"])
    op.create_index("ix_payment_schedule_payment_date", "payment_schedule", ["payment_date"])

    op.create_table(
        "benchmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="EQUITY"),
        sa.Column("engine", sa.String(32), nullable=False, server_default="stock"),
        sa.Column("market", sa.String(32), nullable=False, server_default="index"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "benchmark_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "benchmark_id",
            sa.Integer(),
            sa.ForeignKey("benchmarks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("close", MONEY, nullable=False),
        sa.UniqueConstraint("benchmark_id", "date", name="uq_benchmark_price_date"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("benchmark_prices")
    op.drop_table("benchmarks")
    op.drop_table("payment_schedule")
    op.drop_table("quotes")
    op.drop_table("price_history")
    op.drop_table("transactions")
    op.drop_table("import_batches")
    op.drop_index("ix_assets_secid", table_name="assets")
    op.drop_table("assets")
    op.drop_table("portfolios")
