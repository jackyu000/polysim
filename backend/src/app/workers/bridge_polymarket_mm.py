from __future__ import annotations

import os
import time
import uuid
from sqlalchemy import text

from app.core.db import SessionLocal

VENUE = "POLYMARKET"

POLL_SECONDS = float(os.getenv("BRIDGE_POLL_SECONDS", "2.0"))
SPREAD_CENTS = int(os.getenv("BRIDGE_SPREAD_CENTS", "1"))      # 1c each side
SIZE_QTY = int(os.getenv("BRIDGE_SIZE_QTY", "20"))             # shares per quote
STALE_SECONDS = int(os.getenv("BRIDGE_STALE_SECONDS", "10"))   # ignore old quotes

BOT_USER_ID = os.getenv("BRIDGE_BOT_USER_ID")
if not BOT_USER_ID:
    raise RuntimeError("Set BRIDGE_BOT_USER_ID to the bot user's UUID (string).")

def clamp_micros(x: int) -> int:
    return max(0, min(1_000_000, x))

def cents_to_micros(cents: int) -> int:
    return int(cents * 10_000)  # because 1c = 0.01 = 10_000 micros

def price_micros_to_cents(price_micros: int) -> int:
    # match your API logic :contentReference[oaicite:5]{index=5}
    return (price_micros * 100 + 500_000) // 1_000_000

def ensure_position_row(db, market_id: str):
    db.execute(
        text("""
            INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
            VALUES (:uid, :mid, 0, 0, 0, 0, now())
            ON CONFLICT (user_id, market_id) DO NOTHING
        """),
        {"uid": BOT_USER_ID, "mid": market_id},
    )

def seed_bot_inventory(db, market_id: str, seed_qty: int = 200):
    """
    Simple: give the bot inventory so it can post asks.
    (Since you’re simulating, this is fine. Later you can have the bot earn inventory via fills.)
    """
    ensure_position_row(db, market_id)
    db.execute(
        text("""
            UPDATE positions
            SET yes_shares = GREATEST(yes_shares, :q),
                no_shares  = GREATEST(no_shares,  :q),
                updated_at = now()
            WHERE user_id = :uid AND market_id = :mid
        """),
        {"uid": BOT_USER_ID, "mid": market_id, "q": seed_qty},
    )

def cancel_prior_bridge_order(db, market_id: str, outcome: str, side: str):
    row = db.execute(
        text("""
            SELECT order_id::text as order_id
            FROM bridge_orders
            WHERE market_id = :mid AND venue = :v AND outcome = :o AND side = :s
        """),
        {"mid": market_id, "v": VENUE, "o": outcome, "s": side},
    ).mappings().first()
    if not row:
        return

    oid = row["order_id"]

    # Lock order row and release reserved exactly like your cancel endpoint does
    o = db.execute(
        text("""
            SELECT id::text as id, side, outcome, market_id::text as market_id, qty_remaining, status, reserved_cents
            FROM orders
            WHERE id = :oid AND user_id = :uid
            FOR UPDATE
        """),
        {"oid": oid, "uid": BOT_USER_ID},
    ).mappings().first()

    if not o or o["status"] not in ("OPEN", "PARTIAL"):
        # Clean up mapping anyway
        db.execute(
            text("""
                DELETE FROM bridge_orders
                WHERE market_id = :mid AND venue = :v AND outcome = :o AND side = :s
            """),
            {"mid": market_id, "v": VENUE, "o": outcome, "s": side},
        )
        return

    remaining = int(o["qty_remaining"])

    if o["side"] == "BUY":
        release = int(o["reserved_cents"])
        db.execute(
            text("""
                UPDATE accounts
                SET reserved_cents = reserved_cents - :delta,
                    updated_at = now()
                WHERE user_id = :uid
            """),
            {"delta": release, "uid": BOT_USER_ID},
        )
    else:
        ensure_position_row(db, market_id)
        if o["outcome"] == "YES":
            db.execute(
                text("""
                    UPDATE positions
                    SET yes_reserved = yes_reserved - :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                """),
                {"q": remaining, "uid": BOT_USER_ID, "mid": market_id},
            )
        else:
            db.execute(
                text("""
                    UPDATE positions
                    SET no_reserved = no_reserved - :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                """),
                {"q": remaining, "uid": BOT_USER_ID, "mid": market_id},
            )

    db.execute(
        text("""
            UPDATE orders
            SET status = 'CANCELED', qty_remaining = 0, reserved_cents = 0
            WHERE id = :oid
        """),
        {"oid": oid},
    )

    db.execute(
        text("""
            DELETE FROM bridge_orders
            WHERE market_id = :mid AND venue = :v AND outcome = :o AND side = :s
        """),
        {"mid": market_id, "v": VENUE, "o": outcome, "s": side},
    )

def place_bot_order(db, market_id: str, outcome: str, side: str, price_micros: int, qty: int) -> str:
    """
    Inserts an order and reserves funds/shares similarly to your create_order logic :contentReference[oaicite:6]{index=6}.
    (Does NOT run matching here; your existing API endpoint will do matching when users place orders,
     and your market can also match bot orders if you call the same create_order logic in-process later.)
    """
    ensure_position_row(db, market_id)

    price_cents = price_micros_to_cents(price_micros)
    oid = uuid.uuid4()

    if side == "BUY":
        cost = price_cents * qty
        acct = db.execute(
            text("""
                SELECT balance_cents, reserved_cents
                FROM accounts
                WHERE user_id = :uid
                FOR UPDATE
            """),
            {"uid": BOT_USER_ID},
        ).mappings().first()
        if not acct:
            raise RuntimeError("bot account missing")

        available = int(acct["balance_cents"]) - int(acct["reserved_cents"])
        if available < cost:
            return ""  # skip if bot out of funds

        db.execute(
            text("""
                UPDATE accounts
                SET reserved_cents = reserved_cents + :delta,
                    updated_at = now()
                WHERE user_id = :uid
            """),
            {"delta": cost, "uid": BOT_USER_ID},
        )
        reserved_cents = cost

    else:  # SELL
        # Reserve shares
        if outcome == "YES":
            pos = db.execute(
                text("""
                    SELECT yes_shares, yes_reserved
                    FROM positions
                    WHERE user_id = :uid AND market_id = :mid
                    FOR UPDATE
                """),
                {"uid": BOT_USER_ID, "mid": market_id},
            ).mappings().first()
            if (int(pos["yes_shares"]) - int(pos["yes_reserved"])) < qty:
                return ""
            db.execute(
                text("""
                    UPDATE positions
                    SET yes_reserved = yes_reserved + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                """),
                {"q": qty, "uid": BOT_USER_ID, "mid": market_id},
            )
        else:
            pos = db.execute(
                text("""
                    SELECT no_shares, no_reserved
                    FROM positions
                    WHERE user_id = :uid AND market_id = :mid
                    FOR UPDATE
                """),
                {"uid": BOT_USER_ID, "mid": market_id},
            ).mappings().first()
            if (int(pos["no_shares"]) - int(pos["no_reserved"])) < qty:
                return ""
            db.execute(
                text("""
                    UPDATE positions
                    SET no_reserved = no_reserved + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                """),
                {"q": qty, "uid": BOT_USER_ID, "mid": market_id},
            )
        reserved_cents = 0

    db.execute(
        text("""
            INSERT INTO orders (id, market_id, user_id, outcome, side, price_micros, qty, qty_remaining, status, reserved_cents)
            VALUES (:id, :mid, :uid, :outcome, :side, :price, :qty, :qty, 'OPEN', :reserved)
        """),
        {
            "id": oid,
            "mid": market_id,
            "uid": BOT_USER_ID,
            "outcome": outcome,
            "side": side,
            "price": price_micros,
            "qty": qty,
            "reserved": reserved_cents,
        },
    )
    return str(oid)

def remember_bridge_order(db, market_id: str, outcome: str, side: str, order_id: str):
    db.execute(
        text("""
            INSERT INTO bridge_orders (market_id, venue, outcome, side, order_id, updated_at)
            VALUES (:mid, :v, :o, :s, :oid, now())
            ON CONFLICT (market_id, venue, outcome, side) DO UPDATE
            SET order_id = EXCLUDED.order_id,
                updated_at = now()
        """),
        {"mid": market_id, "v": VENUE, "o": outcome, "s": side, "oid": order_id},
    )

def main():
    spread_micros = cents_to_micros(SPREAD_CENTS)
    print(f"✅ Bridge MM starting. poll={POLL_SECONDS}s spread={SPREAD_CENTS}c size={SIZE_QTY}")

    while True:
        t0 = time.time()

        with SessionLocal() as db:
            try:
                # Pull fresh external quotes
                quotes = db.execute(
                    text(f"""
                        SELECT market_id::text as market_id, outcome,
                               best_bid_micros, best_ask_micros, ts
                        FROM external_quotes
                        WHERE venue = :v
                          AND ts > now() - interval '{STALE_SECONDS} seconds'
                    """),
                    {"v": VENUE},
                ).mappings().all()
                print(f"[bridge] fetched quotes: {len(quotes)}")

                # Group by market/outcome
                for q in quotes:
                    market_id = q["market_id"]
                    outcome = q["outcome"]
                    bb = q["best_bid_micros"]
                    ba = q["best_ask_micros"]
                    if bb is None and ba is None:
                        continue
                    if bb is None:
                        bb = ba
                    if ba is None:
                        ba = bb

                    # Seed inventory so bot can post asks (sim mode)
                    seed_bot_inventory(db, market_id)

                    target_bid = clamp_micros(int(bb) - spread_micros)
                    target_ask = clamp_micros(int(ba) + spread_micros)

                    # Ensure non-crossing
                    if target_bid >= target_ask:
                        # widen a bit if spread collapsed
                        target_bid = clamp_micros(int(bb) - 2 * spread_micros)
                        target_ask = clamp_micros(int(ba) + 2 * spread_micros)
                        if target_bid >= target_ask:
                            continue

                    # Cancel + replace bot bid
                    cancel_prior_bridge_order(db, market_id, outcome, "BUY")
                    bid_oid = place_bot_order(db, market_id, outcome, "BUY", target_bid, SIZE_QTY)
                    if bid_oid:
                        remember_bridge_order(db, market_id, outcome, "BUY", bid_oid)
                    else:
                        print(
                            f"[bridge] failed to place BID for market={market_id} "
                            f"outcome={outcome} target_bid={target_bid}"
                        )

                    # Cancel + replace bot ask
                    cancel_prior_bridge_order(db, market_id, outcome, "SELL")
                    ask_oid = place_bot_order(db, market_id, outcome, "SELL", target_ask, SIZE_QTY)
                    if ask_oid:
                        remember_bridge_order(db, market_id, outcome, "SELL", ask_oid)
                    else:
                        print(
                            f"[bridge] failed to place ASK for market={market_id} "
                            f"outcome={outcome} target_ask={target_ask}"
                        )

                db.commit()
            except Exception as e:
                db.rollback()
                print("bridge error:", repr(e))

        elapsed = time.time() - t0
        time.sleep(max(0.0, POLL_SECONDS - elapsed))

if __name__ == "__main__":
    main()
