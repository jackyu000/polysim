"""markets_add_resolved_at

Revision ID: 4d8b0c7f4a11
Revises: 8846718c2edb
Create Date: 2026-03-03 19:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4d8b0c7f4a11"
down_revision: Union[str, Sequence[str], None] = "8846718c2edb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("markets", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("markets", "resolved_at")
