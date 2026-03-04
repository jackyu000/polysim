from __future__ import annotations

import os
import time
import uuid

from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.errors import DomainError
from app.core.security import hash_password
from app.services.exchange import cancel_order as cancel_order_service
from app.services.exchange import create_order as create_order_service

VENUE = "POLYMARKET"
ONE_CENT_MICROS = 10_000

POLL_SECONDS = float(os.getenv("BRIDGE_POLL_SECONDS", "2.0"))
SPREAD_CENTS = int(os.getenv("BRIDGE_SPREAD_CENTS", "1"))
SIZE_QTY = int(os.getenv("BRIDGE_SIZE_QTY", "20"))
STALE_SECONDS = int(os.getenv("BRIDGE_STALE_SECONDS", "30"))
MAX_SPREAD_CENTS = int(os.getenv("BRIDGE_MAX_SPREAD_CENTS", "15"))
SEED_QTY = int(os.getenv("BRIDGE_SEED_QTY", "200"))
BOT_BALANCE_CENTS = int(os.getenv("BRIDGE_BOT_BALANCE_CENTS", "100000000"))

BOT_USER_ID = os.getenv("BRIDGE_BOT_USER_ID")
if not BOT_USER_ID:
    raise RuntimeError("Set BRIDGE_BOT_USER_ID to the bot user's UUID (string).")
try:
    BOT_USER_ID = str(uuid.UUID(BOT_USER_ID))
except ValueError as exc:
    raise RuntimeError("BRIDGE_BOT_USER_ID must be a valid UUID string.") from exc

BOT_EMAIL = os.getenv("BRIDGE_BOT_EMAIL", f"bridge-bot+{BOT_USER_ID}@local").strip().lower()


def clamp_micros(x: int) -> int:
    return max(0, min(1_000_000, x))


def cents_to_micros(cents: int) -> int:
    return cents * ONE_CENT_MICROS


def ensure_bot_identity(db) -> None:
    existing_user = db.execute(
        text("SELECT 1 FROM users WHERE id = :uid"),
        {"uid": BOT_USER_ID},
    ).first()

    if not existing_user:
        email_owner = db.execute(
            text("SELECT id::text AS id FROM users WHERE email = :email"),
            {"email": BOT_EMAIL},
        ).mappings().first()
        if email_owner and email_owner["id"] != BOT_USER_ID:
            raise RuntimeError(
                f"BRIDGE_BOT_EMAIL '{BOT_EMAIL}' belongs to a different user ({email_owner['id']})."
            )

        db.execute(
            text(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (:uid, :email, :password_hash)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "uid": BOT_USER_ID,
                "email": BOT_EMAIL,
                "password_hash": hash_password(uuid.uuid4().hex),
            },
        )

    db.execute(
        text(
            """
            INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
            VALUES (:uid, :bal, 0, now())
            ON CONFLICT (user_id) DO UPDATE
            SET balance_cents = GREATEST(accounts.balance_cents, EXCLUDED.balance_cents),
                updated_at = now()
            """
        ),
        {"uid": BOT_USER_ID, "bal": BOT_BALANCE_CENTS},
    )


def ensure_position_row(db, market_id: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
            VALUES (:uid, :mid, 0, 0, 0, 0, now())
            ON CONFLICT (user_id, market_id) DO NOTHING
            """
        ),
        {"uid": BOT_USER_ID, "mid": market_id},
    )


def seed_bot_inventory(db, market_id: str, seed_qty: int) -> None:
    ensure_position_row(db, market_id)
    db.execute(
        text(
            """
            UPDATE positions
            SET yes_shares = GREATEST(yes_shares, :q),
                no_shares = GREATEST(no_shares, :q),
                updated_at = now()
            WHERE user_id = :uid AND market_id = :mid
            """
        ),
        {"uid": BOT_USER_ID, "mid": market_id, "q": seed_qty},
    )


def get_tracked_order(db, market_id: str, outcome: str, side: str) -> str | None:
    row = db.execute(
        text(
            """
            SELECT order_id::text AS order_id
            FROM bridge_orders
            WHERE market_id = :mid AND venue = :venue AND outcome = :outcome AND side = :side
            """
        ),
        {"mid": market_id, "venue": VENUE, "outcome": outcome, "side": side},
    ).mappings().first()
    return row["order_id"] if row else None


def get_order_price_and_status(db, order_id: str) -> tuple[int | None, str | None, int | None]:
    row = db.execute(
        text(
            """
            SELECT price_micros, status, qty_remaining
            FROM orders
            WHERE id = :oid
            """
        ),
        {"oid": order_id},
    ).mappings().first()
    if not row:
        return None, None, None
    return int(row["price_micros"]), row["status"], int(row["qty_remaining"])


def remember_bridge_order(db, market_id: str, outcome: str, side: str, order_id: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO bridge_orders (market_id, venue, outcome, side, order_id, updated_at)
            VALUES (:mid, :venue, :outcome, :side, :oid, now())
            ON CONFLICT (market_id, venue, outcome, side) DO UPDATE
            SET order_id = EXCLUDED.order_id,
                updated_at = now()
            """
        ),
        {"mid": market_id, "venue": VENUE, "outcome": outcome, "side": side, "oid": order_id},
    )


def delete_bridge_order(db, market_id: str, outcome: str, side: str) -> None:
    db.execute(
        text(
            """
            DELETE FROM bridge_orders
            WHERE market_id = :mid AND venue = :venue AND outcome = :outcome AND side = :side
            """
        ),
        {"mid": market_id, "venue": VENUE, "outcome": outcome, "side": side},
    )


def reconcile_side(db, *, market_id: str, outcome: str, side: str, target_price: int) -> tuple[str, str | None]:
    tracked_order_id = get_tracked_order(db, market_id, outcome, side)
    had_tracked_order = tracked_order_id is not None

    if tracked_order_id:
        current_price, current_status, current_remaining = get_order_price_and_status(db, tracked_order_id)
        if (
            current_status in ("OPEN", "PARTIAL")
            and current_price is not None
            and current_remaining == SIZE_QTY
            and abs(current_price - target_price) < ONE_CENT_MICROS
        ):
            return "reused", None

        if current_status in ("OPEN", "PARTIAL"):
            try:
                cancel_order_service(db, user_id=BOT_USER_ID, order_id=tracked_order_id)
            except DomainError as exc:
                delete_bridge_order(db, market_id, outcome, side)
                return "failed", str(exc)
        delete_bridge_order(db, market_id, outcome, side)

    try:
        order = create_order_service(
            db,
            user_id=BOT_USER_ID,
            market_id=market_id,
            outcome=outcome,
            side=side,
            price_micros=target_price,
            qty=SIZE_QTY,
        )
    except DomainError as exc:
        return "failed", str(exc)

    remember_bridge_order(db, market_id, outcome, side, order["id"])
    return ("replaced" if had_tracked_order else "placed"), None


def main():
    with SessionLocal() as db:
        try:
            ensure_bot_identity(db)
            db.commit()
            print(
                f"[bridge] bot ready user_id={BOT_USER_ID} email={BOT_EMAIL} "
                f"balance_cents>={BOT_BALANCE_CENTS}"
            )
        except Exception:
            db.rollback()
            raise

    spread_micros = cents_to_micros(SPREAD_CENTS)
    max_spread_micros = cents_to_micros(MAX_SPREAD_CENTS)
    print(
        f"✅ Bridge MM starting. poll={POLL_SECONDS}s spread={SPREAD_CENTS}c "
        f"size={SIZE_QTY} stale={STALE_SECONDS}s max_spread={MAX_SPREAD_CENTS}c"
    )

    while True:
        t0 = time.time()

        with SessionLocal() as db:
            try:
                quotes = db.execute(
                    text(
                        """
                        SELECT q.market_id::text AS market_id,
                               q.outcome,
                               q.best_bid_micros,
                               q.best_ask_micros,
                               q.last_trade_micros,
                               q.ts
                        FROM external_quotes q
                        JOIN markets m ON m.id = q.market_id
                        WHERE q.venue = :venue
                          AND m.status = 'OPEN'
                          AND q.ts > clock_timestamp() - make_interval(secs => :stale_seconds)
                        """
                    ),
                    {"venue": VENUE, "stale_seconds": STALE_SECONDS},
                ).mappings().all()

                stats = {
                    "fetched": len(quotes),
                    "skipped_wide": 0,
                    "skipped_missing_anchor": 0,
                    "placed": 0,
                    "reused": 0,
                    "replaced": 0,
                    "failed": 0,
                }

                for quote in quotes:
                    market_id = quote["market_id"]
                    outcome = quote["outcome"]
                    bid = quote["best_bid_micros"]
                    ask = quote["best_ask_micros"]
                    last_trade = quote["last_trade_micros"]

                    if bid is not None and ask is not None and (int(ask) - int(bid)) > max_spread_micros:
                        stats["skipped_wide"] += 1
                        continue

                    if bid is None:
                        bid = last_trade
                    if ask is None:
                        ask = last_trade
                    if bid is None or ask is None:
                        stats["skipped_missing_anchor"] += 1
                        continue

                    seed_bot_inventory(db, market_id, SEED_QTY)

                    target_bid = clamp_micros(int(bid) - spread_micros)
                    target_ask = clamp_micros(int(ask) + spread_micros)
                    if target_bid >= target_ask:
                        target_bid = clamp_micros(int(bid) - 2 * spread_micros)
                        target_ask = clamp_micros(int(ask) + 2 * spread_micros)
                        if target_bid >= target_ask:
                            stats["skipped_missing_anchor"] += 1
                            continue

                    for side, target_price in (("BUY", target_bid), ("SELL", target_ask)):
                        action, reason = reconcile_side(
                            db,
                            market_id=market_id,
                            outcome=outcome,
                            side=side,
                            target_price=target_price,
                        )
                        if action in stats:
                            stats[action] += 1
                        if reason:
                            print(
                                f"[bridge] failed to place {side} for market={market_id} "
                                f"outcome={outcome} target={target_price} reason={reason}"
                            )

                db.commit()
                print(
                    "[bridge] "
                    f"fetched={stats['fetched']} skipped_wide={stats['skipped_wide']} "
                    f"skipped_missing_anchor={stats['skipped_missing_anchor']} placed={stats['placed']} "
                    f"reused={stats['reused']} replaced={stats['replaced']} failed={stats['failed']}"
                )
            except Exception as e:
                db.rollback()
                print("bridge error:", repr(e))

        elapsed = time.time() - t0
        time.sleep(max(0.0, POLL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
