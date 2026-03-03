"""external_polymarket_cache

Revision ID: 8846718c2edb
Revises: 842d9b58672d
Create Date: 2026-03-03 17:25:56.927390

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8846718c2edb'
down_revision: Union[str, Sequence[str], None] = '842d9b58672d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "external_market_map",
        sa.Column("market_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("venue", sa.Text(), primary_key=True),  # 'POLYMARKET'
        sa.Column("yes_token_id", sa.Text(), nullable=False),
        sa.Column("no_token_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_external_market_map_venue", "external_market_map", ["venue"], unique=False)

    op.create_table(
        "external_quotes",
        sa.Column("market_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("markets.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("venue", sa.Text(), primary_key=True),    # 'POLYMARKET'
        sa.Column("outcome", sa.Text(), primary_key=True),  # 'YES'/'NO'
        sa.Column("best_bid_micros", sa.Integer(), nullable=True),
        sa.Column("best_ask_micros", sa.Integer(), nullable=True),
        sa.Column("last_trade_micros", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_external_quotes_venue_ts", "external_quotes", ["venue", sa.text("ts DESC")], unique=False)

def downgrade():
    op.drop_index("ix_external_quotes_venue_ts", table_name="external_quotes")
    op.drop_table("external_quotes")
    op.drop_index("ix_external_market_map_venue", table_name="external_market_map")
    op.drop_table("external_market_map")