"""positions_add_reserved

Revision ID: d907fce4db40
Revises: 1c0d0ff541ea
Create Date: 2026-03-01 16:45:17.259988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd907fce4db40'
down_revision: Union[str, Sequence[str], None] = '1c0d0ff541ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("positions", sa.Column("yes_reserved", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("positions", sa.Column("no_reserved", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("positions", "yes_reserved", server_default=None)
    op.alter_column("positions", "no_reserved", server_default=None)

def downgrade():
    op.drop_column("positions", "no_reserved")
    op.drop_column("positions", "yes_reserved")
