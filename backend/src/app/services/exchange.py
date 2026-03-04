from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import (
    ForbiddenOrderAccess,
    InsufficientBalance,
    InsufficientInventory,
    InvalidOrder,
    MarketNotFound,
    MarketNotOpen,
    OrderNotCancelable,
    OrderNotFound,
)


def price_micros_to_cents(price_micros: int) -> int:
    return (price_micros * 100 + 500_000) // 1_000_000


def ensure_position_row(db: Session, user_id: str, market_id: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
            VALUES (:uid, :mid, 0, 0, 0, 0, now())
            ON CONFLICT (user_id, market_id) DO NOTHING
            """
        ),
        {"uid": user_id, "mid": market_id},
    )


def create_order(
    db: Session,
    *,
    user_id: str,
    market_id: str,
    outcome: str,
    side: str,
    price_micros: int,
    qty: int,
) -> dict:
    market = db.execute(
        text("SELECT status FROM markets WHERE id = :id"),
        {"id": market_id},
    ).mappings().first()
    if not market:
        raise MarketNotFound("market not found")
    if market["status"] != "OPEN":
        raise MarketNotOpen("market not open")

    if outcome not in ("YES", "NO") or side not in ("BUY", "SELL"):
        raise InvalidOrder("bad outcome/side")
    if price_micros < 0 or price_micros > 1_000_000 or qty < 1:
        raise InvalidOrder("bad price or quantity")

    taker_id = uuid.uuid4()
    taker_price_cents = price_micros_to_cents(price_micros)

    ensure_position_row(db, user_id, market_id)

    if side == "BUY":
        cost_cents = taker_price_cents * qty
        acct = db.execute(
            text(
                """
                SELECT balance_cents, reserved_cents
                FROM accounts
                WHERE user_id = :uid
                FOR UPDATE
                """
            ),
            {"uid": user_id},
        ).mappings().first()
        if not acct:
            raise InsufficientBalance("account missing")

        available = int(acct["balance_cents"]) - int(acct["reserved_cents"])
        if available < cost_cents:
            raise InsufficientBalance("insufficient available balance")

        db.execute(
            text(
                """
                UPDATE accounts
                SET reserved_cents = reserved_cents + :delta,
                    updated_at = now()
                WHERE user_id = :uid
                """
            ),
            {"delta": cost_cents, "uid": user_id},
        )
        taker_reserved_cents = cost_cents
    else:
        if outcome == "YES":
            pos = db.execute(
                text(
                    """
                    SELECT yes_shares, yes_reserved
                    FROM positions
                    WHERE user_id = :uid AND market_id = :mid
                    FOR UPDATE
                    """
                ),
                {"uid": user_id, "mid": market_id},
            ).mappings().first()
            if (int(pos["yes_shares"]) - int(pos["yes_reserved"])) < qty:
                raise InsufficientInventory("insufficient YES shares to sell")
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET yes_reserved = yes_reserved + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": qty, "uid": user_id, "mid": market_id},
            )
        else:
            pos = db.execute(
                text(
                    """
                    SELECT no_shares, no_reserved
                    FROM positions
                    WHERE user_id = :uid AND market_id = :mid
                    FOR UPDATE
                    """
                ),
                {"uid": user_id, "mid": market_id},
            ).mappings().first()
            if (int(pos["no_shares"]) - int(pos["no_reserved"])) < qty:
                raise InsufficientInventory("insufficient NO shares to sell")
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET no_reserved = no_reserved + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": qty, "uid": user_id, "mid": market_id},
            )
        taker_reserved_cents = 0

    db.execute(
        text(
            """
            INSERT INTO orders (id, market_id, user_id, outcome, side, price_micros, qty, qty_remaining, status, reserved_cents)
            VALUES (:id, :mid, :uid, :outcome, :side, :price, :qty, :qty, 'OPEN', :reserved)
            """
        ),
        {
            "id": taker_id,
            "mid": market_id,
            "uid": user_id,
            "outcome": outcome,
            "side": side,
            "price": price_micros,
            "qty": qty,
            "reserved": taker_reserved_cents,
        },
    )

    taker_remaining = qty

    while taker_remaining > 0:
        if side == "BUY":
            maker = db.execute(
                text(
                    """
                    SELECT id::text as id, user_id::text as user_id, price_micros, qty_remaining
                    FROM orders
                    WHERE market_id = :mid AND outcome = :outcome
                      AND side = 'SELL' AND status IN ('OPEN','PARTIAL')
                      AND user_id <> :taker_uid
                      AND price_micros <= :taker_price
                    ORDER BY price_micros ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {
                    "mid": market_id,
                    "outcome": outcome,
                    "taker_price": price_micros,
                    "taker_uid": user_id,
                },
            ).mappings().first()
        else:
            maker = db.execute(
                text(
                    """
                    SELECT id::text as id, user_id::text as user_id, price_micros, qty_remaining
                    FROM orders
                    WHERE market_id = :mid AND outcome = :outcome
                      AND side = 'BUY' AND status IN ('OPEN','PARTIAL')
                      AND user_id <> :taker_uid
                      AND price_micros >= :taker_price
                    ORDER BY price_micros DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {
                    "mid": market_id,
                    "outcome": outcome,
                    "taker_price": price_micros,
                    "taker_uid": user_id,
                },
            ).mappings().first()

        if not maker:
            break

        maker_remaining = int(maker["qty_remaining"])
        trade_qty = min(taker_remaining, maker_remaining)
        trade_price_micros = int(maker["price_micros"])
        fill_cents = price_micros_to_cents(trade_price_micros)

        db.execute(
            text(
                """
                INSERT INTO trades (id, market_id, maker_order_id, taker_order_id, price_micros, qty)
                VALUES (:id, :mid, :maker, :taker, :price, :qty)
                """
            ),
            {
                "id": uuid.uuid4(),
                "mid": market_id,
                "maker": maker["id"],
                "taker": str(taker_id),
                "price": trade_price_micros,
                "qty": trade_qty,
            },
        )

        if side == "BUY":
            buyer_id = user_id
            seller_id = maker["user_id"]
            buyer_limit_cents = taker_price_cents
        else:
            buyer_id = maker["user_id"]
            seller_id = user_id
            buyer_limit_cents = price_micros_to_cents(trade_price_micros)

        db.execute(
            text(
                """
                UPDATE accounts
                SET balance_cents = balance_cents - :spend,
                    reserved_cents = reserved_cents - :release,
                    updated_at = now()
                WHERE user_id = :uid
                """
            ),
            {
                "spend": fill_cents * trade_qty,
                "release": buyer_limit_cents * trade_qty,
                "uid": buyer_id,
            },
        )

        buyer_order_id = taker_id if side == "BUY" else maker["id"]
        db.execute(
            text(
                """
                UPDATE orders
                SET reserved_cents = reserved_cents - :release
                WHERE id = :oid
                """
            ),
            {"release": buyer_limit_cents * trade_qty, "oid": buyer_order_id},
        )

        db.execute(
            text(
                """
                UPDATE accounts
                SET balance_cents = balance_cents + :earn,
                    updated_at = now()
                WHERE user_id = :uid
                """
            ),
            {"earn": fill_cents * trade_qty, "uid": seller_id},
        )

        ensure_position_row(db, buyer_id, market_id)
        ensure_position_row(db, seller_id, market_id)

        if outcome == "YES":
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET yes_shares = yes_shares + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": trade_qty, "uid": buyer_id, "mid": market_id},
            )
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET yes_shares = yes_shares - :q,
                        yes_reserved = yes_reserved - :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": trade_qty, "uid": seller_id, "mid": market_id},
            )
        else:
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET no_shares = no_shares + :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": trade_qty, "uid": buyer_id, "mid": market_id},
            )
            db.execute(
                text(
                    """
                    UPDATE positions
                    SET no_shares = no_shares - :q,
                        no_reserved = no_reserved - :q,
                        updated_at = now()
                    WHERE user_id = :uid AND market_id = :mid
                    """
                ),
                {"q": trade_qty, "uid": seller_id, "mid": market_id},
            )

        new_maker_remaining = maker_remaining - trade_qty
        maker_status = "FILLED" if new_maker_remaining == 0 else "PARTIAL"
        db.execute(
            text(
                """
                UPDATE orders
                SET qty_remaining = :r,
                    status = :st
                WHERE id = :oid
                """
            ),
            {"r": new_maker_remaining, "st": maker_status, "oid": maker["id"]},
        )

        taker_remaining -= trade_qty
        taker_status = "FILLED" if taker_remaining == 0 else "PARTIAL"
        db.execute(
            text(
                """
                UPDATE orders
                SET qty_remaining = :r,
                    status = :st
                WHERE id = :oid
                """
            ),
            {"r": taker_remaining, "st": taker_status, "oid": taker_id},
        )

    row = db.execute(
        text(
            """
            SELECT id::text, market_id::text, outcome, side, price_micros, qty, qty_remaining, status, created_at::text
            FROM orders
            WHERE id = :id
            """
        ),
        {"id": taker_id},
    ).mappings().first()
    return dict(row)


def cancel_order(
    db: Session,
    *,
    user_id: str,
    order_id: str,
    require_owner: bool = True,
) -> dict:
    order = db.execute(
        text(
            """
            SELECT id::text as id, user_id::text as user_id, market_id::text as market_id,
                   outcome, side, price_micros, qty_remaining, status, reserved_cents
            FROM orders
            WHERE id = :oid
            FOR UPDATE
            """
        ),
        {"oid": order_id},
    ).mappings().first()

    if not order:
        raise OrderNotFound("order not found")
    if require_owner and order["user_id"] != user_id:
        raise ForbiddenOrderAccess("not your order")
    if order["status"] not in ("OPEN", "PARTIAL"):
        raise OrderNotCancelable("order not cancelable")

    remaining = int(order["qty_remaining"])
    owner_id = order["user_id"]

    if order["side"] == "BUY":
        db.execute(
            text(
                """
                UPDATE accounts
                SET reserved_cents = reserved_cents - :delta,
                    updated_at = now()
                WHERE user_id = :uid
                """
            ),
            {"delta": int(order["reserved_cents"]), "uid": owner_id},
        )
    else:
        ensure_position_row(db, owner_id, order["market_id"])
        reserved_field = "yes_reserved" if order["outcome"] == "YES" else "no_reserved"
        db.execute(
            text(
                f"""
                UPDATE positions
                SET {reserved_field} = {reserved_field} - :q,
                    updated_at = now()
                WHERE user_id = :uid AND market_id = :mid
                """
            ),
            {"q": remaining, "uid": owner_id, "mid": order["market_id"]},
        )

    db.execute(
        text(
            """
            UPDATE orders
            SET status = 'CANCELED', qty_remaining = 0, reserved_cents = 0
            WHERE id = :oid
            """
        ),
        {"oid": order_id},
    )
    return {"ok": True}


def get_order_book(db: Session, *, market_id: str, outcome: str, depth: int) -> dict:
    if outcome not in ("YES", "NO"):
        raise InvalidOrder("outcome must be YES or NO")

    depth = max(1, min(depth, 100))

    bids = db.execute(
        text(
            """
            SELECT price_micros, qty_remaining, created_at::text
            FROM orders
            WHERE market_id = :mid AND outcome = :outcome
              AND side = 'BUY' AND status IN ('OPEN','PARTIAL')
            ORDER BY price_micros DESC, created_at ASC
            LIMIT :lim
            """
        ),
        {"mid": market_id, "outcome": outcome, "lim": depth},
    ).mappings().all()

    asks = db.execute(
        text(
            """
            SELECT price_micros, qty_remaining, created_at::text
            FROM orders
            WHERE market_id = :mid AND outcome = :outcome
              AND side = 'SELL' AND status IN ('OPEN','PARTIAL')
            ORDER BY price_micros ASC, created_at ASC
            LIMIT :lim
            """
        ),
        {"mid": market_id, "outcome": outcome, "lim": depth},
    ).mappings().all()

    return {"market_id": market_id, "outcome": outcome, "bids": bids, "asks": asks}
