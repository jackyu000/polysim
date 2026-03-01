"""orders_trades_positions

Revision ID: 8f235eeab3e1
Revises: acd967f71273
Create Date: 2026-03-01 12:41:18.809126

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8f235eeab3e1'
down_revision: Union[str, Sequence[str], None] = 'acd967f71273'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ORDERS
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),   # YES / NO
        sa.Column("side", sa.Text(), nullable=False),      # BUY / SELL
        sa.Column("price_micros", sa.Integer(), nullable=False),  # 0..1_000_000
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("qty_remaining", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),    # OPEN / PARTIAL / FILLED / CANCELED
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Useful general indexes
    op.create_index("ix_orders_user_created_at", "orders", ["user_id", "created_at"], unique=False)
    op.create_index("ix_orders_market_created_at", "orders", ["market_id", "created_at"], unique=False)

    # Partial indexes for order book (Postgres-only)
    # Bids (BUY): best price highest first, then earliest time
    op.create_index(
        "ix_orders_book_bids",
        "orders",
        ["market_id", "outcome", sa.text("price_micros DESC"), sa.text("created_at ASC")],
        unique=False,
        postgresql_where=sa.text("side = 'BUY' AND status IN ('OPEN','PARTIAL')")
    )

    # Asks (SELL): best price lowest first, then earliest time
    op.create_index(
        "ix_orders_book_asks",
        "orders",
        ["market_id", "outcome", sa.text("price_micros ASC"), sa.text("created_at ASC")],
        unique=False,
        postgresql_where=sa.text("side = 'SELL' AND status IN ('OPEN','PARTIAL')")
    )

    # TRADES (will be empty until matching step)
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("maker_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taker_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_micros", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_trades_market_ts", "trades", ["market_id", sa.text("ts DESC")], unique=False)

    # POSITIONS (for SELL later; can start at 0)
    op.create_table(
        "positions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("yes_shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("positions")

    op.drop_index("ix_trades_market_ts", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_orders_book_asks", table_name="orders")
    op.drop_index("ix_orders_book_bids", table_name="orders")
    op.drop_index("ix_orders_market_created_at", table_name="orders")
    op.drop_index("ix_orders_user_created_at", table_name="orders")
    op.drop_table("orders")