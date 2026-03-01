import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import text
from app.core.db import SessionLocal
from app.core.security import hash_password


def main() -> None:
    db = SessionLocal()
    try:
        slug = "will-bitcoin-close-above-100k-this-week"
        resolves_at = datetime.now(timezone.utc) + timedelta(days=7)

        # Market upsert
        db.execute(
            text("""
                INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome, created_at)
                VALUES (:id, :slug, :q, 'OPEN', :resolves_at, NULL, now())
                ON CONFLICT (slug) DO UPDATE
                SET question = EXCLUDED.question,
                    status = 'OPEN',
                    resolves_at = EXCLUDED.resolves_at,
                    resolved_outcome = NULL
            """),
            {"id": uuid.uuid4(), "slug": slug, "q": "Will BTC close above $100k this week?", "resolves_at": resolves_at},
        )

        market = db.execute(
            text("SELECT id::text AS id, slug, status FROM markets WHERE slug = :slug"),
            {"slug": slug},
        ).mappings().first()
        assert market is not None
        market_id = market["id"]

        alice_email = "1@example.com"
        bob_email = "2@example.com"

        alice_pw = "1234"
        bob_pw = "1234"

        # Create users if missing (real password hashes)
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at)
                VALUES (:id, :email, :ph, now())
                ON CONFLICT (email) DO NOTHING
            """),
            [
                {"id": uuid.uuid4(), "email": alice_email, "ph": hash_password(alice_pw)},
                {"id": uuid.uuid4(), "email": bob_email, "ph": hash_password(bob_pw)},
            ],
        )

        users = db.execute(
            text("""
                SELECT id::text AS id, email
                FROM users
                WHERE email IN (:a, :b)
            """),
            {"a": alice_email, "b": bob_email},
        ).mappings().all()

        by_email = {u["email"]: u["id"] for u in users}
        alice_id = by_email[alice_email]
        bob_id = by_email[bob_email]

        # Accounts
        db.execute(
            text("""
                INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
                VALUES (:uid, :bal, 0, now())
                ON CONFLICT (user_id) DO UPDATE
                SET balance_cents = GREATEST(accounts.balance_cents, EXCLUDED.balance_cents),
                    updated_at = now()
            """),
            [{"uid": alice_id, "bal": 100_00}, {"uid": bob_id, "bal": 100_00}],
        )

        # Positions (assumes yes_reserved/no_reserved exist)
        db.execute(
            text("""
                INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
                VALUES (:uid, :mid, 0, 0, 0, 0, now())
                ON CONFLICT (user_id, market_id) DO NOTHING
            """),
            [{"uid": alice_id, "mid": market_id}, {"uid": bob_id, "mid": market_id}],
        )

        # Give Bob YES shares
        db.execute(
            text("""
                UPDATE positions
                SET yes_shares = GREATEST(yes_shares, :yes),
                    updated_at = now()
                WHERE user_id = :uid AND market_id = :mid
            """),
            {"yes": 50, "uid": bob_id, "mid": market_id},
        )

        db.commit()
        print("Seeded:", {"market": market, "alice_email": alice_email, "alice_pw": alice_pw, "bob_email": bob_email, "bob_pw": bob_pw})

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()