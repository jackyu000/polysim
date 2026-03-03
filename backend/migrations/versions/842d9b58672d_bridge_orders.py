"""bridge_orders

Revision ID: 842d9b58672d
Revises: d907fce4db40
Create Date: 2026-03-03 17:03:37.753373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '842d9b58672d'
down_revision: Union[str, Sequence[str], None] = 'd907fce4db40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "bridge_orders",
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("venue", sa.Text(), primary_key=True),    # 'POLYMARKET'
        sa.Column("outcome", sa.Text(), primary_key=True),  # YES/NO
        sa.Column("side", sa.Text(), primary_key=True),     # BUY/SELL
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_bridge_orders_venue", "bridge_orders", ["venue"], unique=False)

def downgrade():
    op.drop_index("ix_bridge_orders_venue", table_name="bridge_orders")
    op.drop_table("bridge_orders")