from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

VENUE = "POLYMARKET"
QUOTE_MARKET_LIMIT = int(os.getenv("POLY_QUOTE_MARKET_LIMIT", "500"))


def get_quote_targets(db: Session, *, limit: int | None = None) -> list[dict]:
    target_limit = limit if limit is not None else QUOTE_MARKET_LIMIT
    rows = db.execute(
        text(
            """
            WITH last_trades AS (
                SELECT market_id, max(ts) AS last_trade_ts
                FROM trades
                GROUP BY market_id
            ),
            last_orders AS (
                SELECT market_id, max(created_at) AS last_order_ts
                FROM orders
                GROUP BY market_id
            )
            SELECT emm.market_id::text AS market_id,
                   emm.yes_token_id,
                   emm.no_token_id
            FROM external_market_map emm
            JOIN markets m ON m.id = emm.market_id
            LEFT JOIN last_trades lt ON lt.market_id = emm.market_id
            LEFT JOIN last_orders lo ON lo.market_id = emm.market_id
            WHERE emm.venue = :venue
              AND m.status = 'OPEN'
            ORDER BY lt.last_trade_ts DESC NULLS LAST,
                     lo.last_order_ts DESC NULLS LAST,
                     m.created_at DESC,
                     m.id DESC
            LIMIT :limit
            """
        ),
        {"venue": VENUE, "limit": target_limit},
    ).mappings().all()
    return [dict(row) for row in rows]
