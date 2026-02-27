import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.core.db import SessionLocal

def main() -> None:
    db = SessionLocal()
    try:
        # Create 2 users
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()

        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash)
                VALUES (:id, :email, :ph)
            """),
            [{"id": u1, "email": "alice@example.com", "ph": "dev_hash"},
             {"id": u2, "email": "bob@example.com", "ph": "dev_hash"}],
        )

        # Give them balances
        db.execute(
            text("""
                INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
                VALUES (:uid, :bal, 0, now())
            """),
            [{"uid": u1, "bal": 100_00}, {"uid": u2, "bal": 100_00}],
        )

        # Create 1 market
        m1 = uuid.uuid4()
        db.execute(
            text("""
                INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome)
                VALUES (:id, :slug, :q, 'OPEN', :resolves_at, NULL)
            """),
            {
                "id": m1,
                "slug": "will-bitcoin-close-above-100k-this-week",
                "q": "Will BTC close above $100k this week?",
                "resolves_at": datetime.now(timezone.utc),
            },
        )

        db.commit()
        print("Seeded:", {"users": [str(u1), str(u2)], "market": str(m1)})

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()