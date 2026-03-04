# app/workers/polymarket_quote_poller.py
from __future__ import annotations

import os
import time
from typing import Any

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal
from app.services.polymarket_targets import get_quote_targets

VENUE = "POLYMARKET"
CLOB_BASE = "https://clob.polymarket.com"

POLL_SECONDS = float(os.getenv("POLY_POLL_SECONDS", "2.0"))
BATCH_SIZE = int(os.getenv("POLY_BATCH_SIZE", "50"))


def dollars_to_micros(v: Any) -> int | None:
    """
    Polymarket prices are typically decimals like "0.52" (dollars).
    Convert to micros: 0.52 -> 520_000
    """
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    f = max(0.0, min(1.0, f))
    return int(round(f * 1_000_000))


def best_from_levels(levels: Any, want: str) -> int | None:
    """
    levels often look like:
      - [[price, size], ...]
      - [{"price":"0.52","size":"10"}, ...]
    Return best bid (max) or best ask (min) in micros.
    """
    if not levels:
        return None

    def price_of(x):
        if isinstance(x, (list, tuple)) and len(x) >= 1:
            return dollars_to_micros(x[0])
        if isinstance(x, dict):
            return dollars_to_micros(x.get("price"))
        return None

    prices = [p for p in (price_of(x) for x in levels) if p is not None]
    if not prices:
        return None
    return max(prices) if want == "bid" else min(prices)


def chunk(xs: list[str], n: int) -> list[list[str]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def upsert_quote(
    db,
    market_id: str,
    outcome: str,
    bb: int | None,
    ba: int | None,
    lt: int | None,
):
    db.execute(
        text(
            """
            INSERT INTO external_quotes (market_id, venue, outcome, best_bid_micros, best_ask_micros, last_trade_micros, ts)
            VALUES (:mid, :v, :o, :bb, :ba, :lt, clock_timestamp())
            ON CONFLICT (market_id, venue, outcome) DO UPDATE
            SET best_bid_micros = EXCLUDED.best_bid_micros,
                best_ask_micros = EXCLUDED.best_ask_micros,
                last_trade_micros = EXCLUDED.last_trade_micros,
                ts = clock_timestamp()
            """
        ),
        {"mid": market_id, "v": VENUE, "o": outcome, "bb": bb, "ba": ba, "lt": lt},
    )


def main():
    client = httpx.Client(base_url=CLOB_BASE, timeout=10.0)
    print(f"✅ Polymarket poller starting. poll={POLL_SECONDS}s batch={BATCH_SIZE}")

    while True:
        t0 = time.time()

        with SessionLocal() as db:
            try:
                maps = get_quote_targets(db)

                # token_id -> (market_id, outcome)
                token_to_ref: dict[str, tuple[str, str]] = {}
                for r in maps:
                    token_to_ref[str(r["yes_token_id"])] = (r["market_id"], "YES")
                    token_to_ref[str(r["no_token_id"])] = (r["market_id"], "NO")

                tokens = list(token_to_ref.keys())
                if not tokens:
                    print("[poller] no mapped tokens")
                    db.commit()
                    time.sleep(max(0.2, POLL_SECONDS))
                    continue

                batches = chunk(tokens, BATCH_SIZE)
                print(
                    f"[poller] selected_markets={len(maps)} tokens={len(tokens)} "
                    f"batches={len(batches)}"
                )

                for idx, batch in enumerate(batches, start=1):
                    try:
                        # IMPORTANT: POST /books expects JSON ARRAY of {"token_id": "..."}
                        payload = [{"token_id": t} for t in batch]

                        resp = client.post("/books", json=payload)
                        resp.raise_for_status()

                        books = resp.json()
                        if not isinstance(books, list):
                            print(
                                f"[poller] batch {idx}/{len(batches)} unexpected response "
                                f"type={type(books).__name__}"
                            )
                            db.rollback()
                            continue

                        upserts = 0
                        for b in books:
                            # docs often use "asset_id" in response; be flexible
                            token_id = str(
                                b.get("asset_id") or b.get("token_id") or b.get("tokenId") or ""
                            )
                            if token_id not in token_to_ref:
                                continue

                            market_id, outcome = token_to_ref[token_id]

                            bids = b.get("bids") or []
                            asks = b.get("asks") or []
                            bb = best_from_levels(bids, "bid")
                            ba = best_from_levels(asks, "ask")

                            lt = dollars_to_micros(
                                b.get("last_trade_price")
                                or b.get("lastTradePrice")
                                or b.get("last_trade")
                                or b.get("lastTrade")
                            )

                            upsert_quote(db, market_id, outcome, bb, ba, lt)
                            upserts += 1

                        db.commit()
                        print(
                            f"[poller] batch {idx}/{len(batches)} committed upserts={upserts}"
                        )
                    except Exception as batch_error:
                        db.rollback()
                        print(
                            f"[poller] batch {idx}/{len(batches)} error: {batch_error!r}"
                        )
            except Exception as e:
                db.rollback()
                print("poller error:", repr(e))

        elapsed = time.time() - t0
        time.sleep(max(0.0, POLL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
