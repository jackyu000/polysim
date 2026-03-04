from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import MarketAlreadyResolved, MarketNotFound


def resolve_market(db: Session, *, market_id: str, resolved_outcome: str) -> dict:
    if resolved_outcome not in ("YES", "NO"):
        raise ValueError("resolved_outcome must be YES or NO")

    market = db.execute(
        text(
            """
            SELECT id::text, status, resolved_outcome, resolved_at::text
            FROM markets
            WHERE id = :mid
            FOR UPDATE
            """
        ),
        {"mid": market_id},
    ).mappings().first()
    if not market:
        raise MarketNotFound("market not found")

    current_outcome = market["resolved_outcome"]
    if current_outcome:
        if current_outcome == resolved_outcome:
            return {
                "market_id": market_id,
                "status": "CLOSED",
                "resolved_outcome": current_outcome,
                "resolved_at": market["resolved_at"],
                "canceled_orders": 0,
                "settled_positions": 0,
                "total_payout_cents": 0,
            }
        raise MarketAlreadyResolved(current_outcome)

    db.execute(
        text(
            """
            UPDATE markets
            SET status = 'CLOSED',
                resolved_outcome = :outcome,
                resolved_at = now()
            WHERE id = :mid
            """
        ),
        {"outcome": resolved_outcome, "mid": market_id},
    )

    open_orders = db.execute(
        text(
            """
            SELECT id::text AS id, user_id::text AS user_id, side, outcome,
                   qty_remaining, reserved_cents
            FROM orders
            WHERE market_id = :mid
              AND status IN ('OPEN', 'PARTIAL')
            FOR UPDATE
            """
        ),
        {"mid": market_id},
    ).mappings().all()

    canceled_orders = len(open_orders)
    buy_releases: dict[str, int] = {}
    sell_releases: dict[tuple[str, str], int] = {}

    for order in open_orders:
        if order["side"] == "BUY":
            buy_releases[order["user_id"]] = buy_releases.get(order["user_id"], 0) + int(
                order["reserved_cents"]
            )
        else:
            key = (order["user_id"], order["outcome"])
            sell_releases[key] = sell_releases.get(key, 0) + int(order["qty_remaining"])

    for user_id, release in buy_releases.items():
        db.execute(
            text(
                """
                UPDATE accounts
                SET reserved_cents = reserved_cents - :release,
                    updated_at = now()
                WHERE user_id = :uid
                """
            ),
            {"release": release, "uid": user_id},
        )

    for (user_id, outcome), release_qty in sell_releases.items():
        reserved_field = "yes_reserved" if outcome == "YES" else "no_reserved"
        db.execute(
            text(
                f"""
                UPDATE positions
                SET {reserved_field} = {reserved_field} - :release_qty,
                    updated_at = now()
                WHERE user_id = :uid AND market_id = :mid
                """
            ),
            {"release_qty": release_qty, "uid": user_id, "mid": market_id},
        )

    db.execute(
        text(
            """
            UPDATE orders
            SET status = 'CANCELED',
                qty_remaining = 0,
                reserved_cents = 0
            WHERE market_id = :mid
              AND status IN ('OPEN', 'PARTIAL')
            """
        ),
        {"mid": market_id},
    )

    settled = db.execute(
        text(
            """
            SELECT user_id::text AS user_id,
                   yes_shares,
                   no_shares
            FROM positions
            WHERE market_id = :mid
            FOR UPDATE
            """
        ),
        {"mid": market_id},
    ).mappings().all()

    total_payout_cents = 0
    settled_positions = 0

    for position in settled:
        winning_shares = int(position["yes_shares"] if resolved_outcome == "YES" else position["no_shares"])
        payout = winning_shares * 100
        if payout:
            db.execute(
                text(
                    """
                    UPDATE accounts
                    SET balance_cents = balance_cents + :payout,
                        updated_at = now()
                    WHERE user_id = :uid
                    """
                ),
                {"payout": payout, "uid": position["user_id"]},
            )
            total_payout_cents += payout
        settled_positions += 1

    db.execute(
        text(
            """
            UPDATE positions
            SET yes_shares = 0,
                no_shares = 0,
                yes_reserved = 0,
                no_reserved = 0,
                updated_at = now()
            WHERE market_id = :mid
            """
        ),
        {"mid": market_id},
    )

    resolved = db.execute(
        text(
            """
            SELECT id::text AS market_id, status, resolved_outcome, resolved_at::text AS resolved_at
            FROM markets
            WHERE id = :mid
            """
        ),
        {"mid": market_id},
    ).mappings().first()

    return {
        "market_id": resolved["market_id"],
        "status": resolved["status"],
        "resolved_outcome": resolved["resolved_outcome"],
        "resolved_at": resolved["resolved_at"],
        "canceled_orders": canceled_orders,
        "settled_positions": settled_positions,
        "total_payout_cents": total_payout_cents,
    }
