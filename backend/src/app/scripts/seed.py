import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from app.core.db import SessionLocal

def main() -> None:
    db = SessionLocal()
    try:
        slug = "will-bitcoin-close-above-100k-this-week"

        # Insert market if it doesn't exist
        db.execute(
            text("""
                INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome, created_at)
                VALUES (:id, :slug, :q, 'OPEN', :resolves_at, NULL, now())
                ON CONFLICT (slug) DO NOTHING
            """),
            {
                "id": uuid.uuid4(),
                "slug": slug,
                "q": "Will BTC close above $100k this week?",
                "resolves_at": datetime.now(timezone.utc) + timedelta(days=7),
            },
        )

        # Fetch its id (works whether inserted now or already existed)
        row = db.execute(
            text("SELECT id::text, slug, status FROM markets WHERE slug = :slug"),
            {"slug": slug},
        ).mappings().first()

        db.commit()
        print("Seeded market:", row)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()