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
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
