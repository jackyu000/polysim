"""orders_add_reserved_cents

Revision ID: 1c0d0ff541ea
Revises: 8f235eeab3e1
Create Date: 2026-03-01 15:46:51.456085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c0d0ff541ea'
down_revision: Union[str, Sequence[str], None] = '8f235eeab3e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # add column with a temporary default so existing rows can be updated
    op.add_column(
        "orders",
        sa.Column("reserved_cents", sa.BigInteger(), nullable=False, server_default="0"),
    )

    # backfill existing BUY open/partial orders so it matches current reservation math
    op.execute(sa.text("""
        UPDATE orders
        SET reserved_cents = ((price_micros * 100 + 500000) / 1000000) * qty_remaining
        WHERE side = 'BUY' AND status IN ('OPEN','PARTIAL')
    """))

    # remove default going forward (we will always set it explicitly in code)
    op.alter_column("orders", "reserved_cents", server_default=None)


def downgrade() -> None:
    op.drop_column("orders", "reserved_cents")
