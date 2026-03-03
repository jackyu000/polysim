# app/scripts/polymarket_sync_markets_and_map.py
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal

GAMMA_BASE = "https://gamma-api.polymarket.com"
VENUE = "POLYMARKET"


def parse_dt(s: str | None) -> datetime | None:
    """
    Gamma commonly returns ISO timestamps with 'Z' suffix. Parse using stdlib only.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def map_status(m: dict[str, Any]) -> str:
    # Simple, stable mapping for now
    return "CLOSED" if bool(m.get("closed")) else "OPEN"


def extract_yes_no_token_ids(m: dict[str, Any]) -> tuple[str, str] | None:
    """
    Gamma `clobTokenIds` is often a JSON-encoded string per API docs, e.g. '["YES_ID","NO_ID"]'.
    Sometimes it's already a list. Normalize to (yes_id, no_id).
    """
    raw = m.get("clobTokenIds")
    if raw is None:
        return None

    # Case 1: already a list
    if isinstance(raw, list) and len(raw) >= 2:
        return str(raw[0]), str(raw[1])

    # Case 2: JSON string
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except Exception:
            return None
        if isinstance(parsed, list) and len(parsed) >= 2:
            return str(parsed[0]), str(parsed[1])

    return None


def main() -> None:
    # Paging + filtering knobs
    limit = int(os.getenv("POLY_SYNC_LIMIT", "200"))
    max_pages = int(os.getenv("POLY_SYNC_MAX_PAGES", "50"))  # 50*200 = 10k max
    active_only = os.getenv("POLY_SYNC_ACTIVE_ONLY", "true").lower() in ("1", "true", "yes")
    require_orderbook = os.getenv("POLY_SYNC_REQUIRE_ORDERBOOK", "true").lower() in ("1", "true", "yes")

    params_base: dict[str, Any] = {"limit": limit, "offset": 0}
    if active_only:
        params_base["active"] = "true"
        params_base["closed"] = "false"

    upserted_markets = 0
    upserted_maps = 0

    with httpx.Client(base_url=GAMMA_BASE, timeout=30.0) as client:
        with SessionLocal() as db:
            try:
                for page in range(max_pages):
                    params = dict(params_base)
                    params["offset"] = page * limit

                    r = client.get("/markets", params=params)
                    r.raise_for_status()
                    markets = r.json()

                    if not isinstance(markets, list) or not markets:
                        break

                    for m in markets:
                        if not isinstance(m, dict):
                            continue

                        slug = m.get("slug")
                        question = m.get("question")
                        if not slug or not question:
                            continue

                        status = map_status(m)
                        resolves_at = parse_dt(m.get("endDateIso") or m.get("endDate"))

                        # 1) Upsert into YOUR markets table by slug (slug is unique) :contentReference[oaicite:0]{index=0}
                        db.execute(
                            text(
                                """
                                INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome)
                                VALUES (:id, :slug, :question, :status, :resolves_at, NULL)
                                ON CONFLICT (slug) DO UPDATE
                                SET question = EXCLUDED.question,
                                    status = EXCLUDED.status,
                                    resolves_at = EXCLUDED.resolves_at
                                """
                            ),
                            {
                                "id": uuid.uuid4(),
                                "slug": slug,
                                "question": question,
                                "status": status,
                                "resolves_at": resolves_at,
                            },
                        )
                        upserted_markets += 1

                        # Read back internal market_id
                        row = db.execute(
                            text("SELECT id::text as id FROM markets WHERE slug = :slug"),
                            {"slug": slug},
                        ).mappings().first()
                        if not row:
                            continue
                        internal_market_id = row["id"]

                        # 2) Upsert external_market_map using clobTokenIds (YES first, NO second)
                        if require_orderbook and (m.get("enableOrderBook") is not True):
                            continue

                        pair = extract_yes_no_token_ids(m)
                        if not pair:
                            continue

                        yes_id, no_id = pair
                        db.execute(
                            text(
                                """
                                INSERT INTO external_market_map (market_id, venue, yes_token_id, no_token_id, updated_at)
                                VALUES (:mid, :venue, :yes, :no, now())
                                ON CONFLICT (market_id, venue) DO UPDATE
                                SET yes_token_id = EXCLUDED.yes_token_id,
                                    no_token_id  = EXCLUDED.no_token_id,
                                    updated_at   = now()
                                """
                            ),
                            {"mid": internal_market_id, "venue": VENUE, "yes": yes_id, "no": no_id},
                        )
                        upserted_maps += 1

                db.commit()
            except Exception:
                db.rollback()
                raise

    print(f"✅ Sync done. markets upserted={upserted_markets}, maps upserted={upserted_maps}")


if __name__ == "__main__":
    main()