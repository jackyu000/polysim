from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def create_user(db: Session, *, email: str, balance_cents: int = 10_000) -> dict:
    user_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash)
            VALUES (:id, :email, :password_hash)
            """
        ),
        {"id": user_id, "email": email, "password_hash": "test-hash"},
    )
    db.execute(
        text(
            """
            INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
            VALUES (:uid, :balance_cents, 0, now())
            """
        ),
        {"uid": user_id, "balance_cents": balance_cents},
    )
    return {"id": user_id, "email": email}


def create_market(
    db: Session,
    *,
    slug: str,
    question: str,
    status: str = "OPEN",
    created_at: str | None = None,
) -> str:
    market_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome, resolved_at, created_at)
            VALUES (
                :id,
                :slug,
                :question,
                :status,
                NULL,
                NULL,
                NULL,
                COALESCE(CAST(:created_at AS timestamptz), now())
            )
            """
        ),
        {
            "id": market_id,
            "slug": slug,
            "question": question,
            "status": status,
            "created_at": created_at,
        },
    )
    return market_id


def ensure_position(
    db: Session,
    *,
    user_id: str,
    market_id: str,
    yes_shares: int = 0,
    no_shares: int = 0,
    yes_reserved: int = 0,
    no_reserved: int = 0,
):
    db.execute(
        text(
            """
            INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
            VALUES (:uid, :mid, :yes_shares, :no_shares, :yes_reserved, :no_reserved, now())
            ON CONFLICT (user_id, market_id) DO UPDATE
            SET yes_shares = EXCLUDED.yes_shares,
                no_shares = EXCLUDED.no_shares,
                yes_reserved = EXCLUDED.yes_reserved,
                no_reserved = EXCLUDED.no_reserved,
                updated_at = now()
            """
        ),
        {
            "uid": user_id,
            "mid": market_id,
            "yes_shares": yes_shares,
            "no_shares": no_shares,
            "yes_reserved": yes_reserved,
            "no_reserved": no_reserved,
        },
    )
