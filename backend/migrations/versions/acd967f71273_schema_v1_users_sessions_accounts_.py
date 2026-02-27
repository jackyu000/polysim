"""schema_v1_users_sessions_accounts_markets

Revision ID: acd967f71273
Revises: 
Create Date: 2026-02-22 15:28:20.438344

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'acd967f71273'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # USERS
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_email_unique", "users", ["email"], unique=True)

    # SESSIONS (refresh tokens)
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
    )
    op.create_index("ix_sessions_refresh_token_hash_unique", "sessions", ["refresh_token_hash"], unique=True)
    op.create_index("ix_sessions_user_id_created_at", "sessions", ["user_id", "created_at"], unique=False)

    # ACCOUNTS (play money wallet)
    op.create_table(
        "accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # MARKETS
    op.create_table(
        "markets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),  # OPEN/CLOSED/RESOLVED
        sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_outcome", sa.Text(), nullable=True),  # YES/NO
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_markets_slug_unique", "markets", ["slug"], unique=True)
    op.create_index("ix_markets_status_resolves_at", "markets", ["status", "resolves_at"], unique=False)
    op.create_index("ix_markets_created_at", "markets", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_markets_created_at", table_name="markets")
    op.drop_index("ix_markets_status_resolves_at", table_name="markets")
    op.drop_index("ix_markets_slug_unique", table_name="markets")
    op.drop_table("markets")

    op.drop_table("accounts")

    op.drop_index("ix_sessions_user_id_created_at", table_name="sessions")
    op.drop_index("ix_sessions_refresh_token_hash_unique", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_users_email_unique", table_name="users")
    op.drop_table("users")