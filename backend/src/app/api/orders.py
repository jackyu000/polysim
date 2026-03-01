import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.auth_deps import get_current_user
from app.schemas.orders import OrderCreateIn, OrderOut

router = APIRouter(prefix="/api", tags=["orders"])

def price_micros_to_cents(price_micros: int) -> int:
    # round to nearest cent
    return (price_micros * 100 + 500_000) // 1_000_000

@router.post("/orders", response_model=OrderOut)
def create_order(payload: OrderCreateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # validate market exists + OPEN
    m = db.execute(
        text("SELECT status FROM markets WHERE id = :id"),
        {"id": payload.market_id},
    ).mappings().first()
    if not m:
        raise HTTPException(status_code=404, detail="market not found")
    if m["status"] != "OPEN":
        raise HTTPException(status_code=400, detail="market not open")

    if payload.outcome not in ("YES", "NO") or payload.side not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="bad outcome/side")

    taker_id = uuid.uuid4()
    taker_price_cents = price_micros_to_cents(payload.price_micros)

    try:
        # Ensure positions row exists
        db.execute(
            text("""
                INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
                VALUES (:uid, :mid, 0, 0, 0, 0, now())
                ON CONFLICT (user_id, market_id) DO NOTHING
            """),
            {"uid": user["id"], "mid": payload.market_id},
        )

        # Reserve depending on side
        if payload.side == "BUY":
            cost_cents = taker_price_cents * payload.qty

            acct = db.execute(
                text("""
                    SELECT balance_cents, reserved_cents
                    FROM accounts
                    WHERE user_id = :uid
                    FOR UPDATE
                """),
                {"uid": user["id"]},
            ).mappings().first()
            if not acct:
                raise HTTPException(status_code=400, detail="account missing")

            available = acct["balance_cents"] - acct["reserved_cents"]
            if available < cost_cents:
                raise HTTPException(status_code=400, detail="insufficient available balance")

            db.execute(
                text("""
                    UPDATE accounts
                    SET reserved_cents = reserved_cents + :delta,
                        updated_at = now()
                    WHERE user_id = :uid
                """),
                {"delta": cost_cents, "uid": user["id"]},
            )

            taker_reserved_cents = cost_cents

        else:  # SELL
            # Reserve shares
            if payload.outcome == "YES":
                pos = db.execute(
                    text("""
                        SELECT yes_shares, yes_reserved
                        FROM positions
                        WHERE user_id = :uid AND market_id = :mid
                        FOR UPDATE
                    """),
                    {"uid": user["id"], "mid": payload.market_id},
                ).mappings().first()
                if (pos["yes_shares"] - pos["yes_reserved"]) < payload.qty:
                    raise HTTPException(status_code=400, detail="insufficient YES shares to sell")
                db.execute(
                    text("""
                        UPDATE positions
                        SET yes_reserved = yes_reserved + :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": payload.qty, "uid": user["id"], "mid": payload.market_id},
                )
            else:
                pos = db.execute(
                    text("""
                        SELECT no_shares, no_reserved
                        FROM positions
                        WHERE user_id = :uid AND market_id = :mid
                        FOR UPDATE
                    """),
                    {"uid": user["id"], "mid": payload.market_id},
                ).mappings().first()
                if (pos["no_shares"] - pos["no_reserved"]) < payload.qty:
                    raise HTTPException(status_code=400, detail="insufficient NO shares to sell")
                db.execute(
                    text("""
                        UPDATE positions
                        SET no_reserved = no_reserved + :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": payload.qty, "uid": user["id"], "mid": payload.market_id},
                )

            taker_reserved_cents = 0

        # Insert taker order
        db.execute(
            text("""
                INSERT INTO orders (id, market_id, user_id, outcome, side, price_micros, qty, qty_remaining, status, reserved_cents)
                VALUES (:id, :mid, :uid, :outcome, :side, :price, :qty, :qty, 'OPEN', :reserved)
            """),
            {
                "id": taker_id,
                "mid": payload.market_id,
                "uid": user["id"],
                "outcome": payload.outcome,
                "side": payload.side,
                "price": payload.price_micros,
                "qty": payload.qty,
                "reserved": taker_reserved_cents,
            },
        )

        taker_remaining = payload.qty

        # Matching loop
        while taker_remaining > 0:
            if payload.side == "BUY":
                maker = db.execute(
                    text("""
                        SELECT id::text as id, user_id::text as user_id, price_micros, qty_remaining
                        FROM orders
                        WHERE market_id = :mid AND outcome = :outcome
                          AND side = 'SELL' AND status IN ('OPEN','PARTIAL')
                          AND price_micros <= :taker_price
                        ORDER BY price_micros ASC, created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    """),
                    {"mid": payload.market_id, "outcome": payload.outcome, "taker_price": payload.price_micros},
                ).mappings().first()
            else:  # SELL
                maker = db.execute(
                    text("""
                        SELECT id::text as id, user_id::text as user_id, price_micros, qty_remaining, reserved_cents
                        FROM orders
                        WHERE market_id = :mid AND outcome = :outcome
                          AND side = 'BUY' AND status IN ('OPEN','PARTIAL')
                          AND price_micros >= :taker_price
                        ORDER BY price_micros DESC, created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    """),
                    {"mid": payload.market_id, "outcome": payload.outcome, "taker_price": payload.price_micros},
                ).mappings().first()

            if not maker:
                break

            maker_remaining = int(maker["qty_remaining"])
            trade_qty = taker_remaining if taker_remaining < maker_remaining else maker_remaining

            trade_price_micros = int(maker["price_micros"])  # maker price
            fill_cents = price_micros_to_cents(trade_price_micros)

            trade_id = uuid.uuid4()
            db.execute(
                text("""
                    INSERT INTO trades (id, market_id, maker_order_id, taker_order_id, price_micros, qty)
                    VALUES (:id, :mid, :maker, :taker, :price, :qty)
                """),
                {
                    "id": trade_id,
                    "mid": payload.market_id,
                    "maker": maker["id"],
                    "taker": str(taker_id),
                    "price": trade_price_micros,
                    "qty": trade_qty,
                },
            )

            # Identify buyer/seller for settlement
            if payload.side == "BUY":
                buyer_id = user["id"]
                seller_id = maker["user_id"]
                buyer_limit_cents = taker_price_cents
                seller_is_maker = True
            else:
                buyer_id = maker["user_id"]
                seller_id = user["id"]
                buyer_limit_cents = price_micros_to_cents(int(maker["price_micros"]))
                seller_is_maker = False

            # --- SETTLEMENT ---
            # Buyer: spend fill price, release reserved at buyer limit
            db.execute(
                text("""
                    UPDATE accounts
                    SET balance_cents = balance_cents - :spend,
                        reserved_cents = reserved_cents - :release,
                        updated_at = now()
                    WHERE user_id = :uid
                """),
                {
                    "spend": fill_cents * trade_qty,
                    "release": buyer_limit_cents * trade_qty,
                    "uid": buyer_id,
                },
            )

            # Also decrement reserved_cents on buyer order (the BUY order involved)
            if payload.side == "BUY":
                # taker is buyer
                db.execute(
                    text("""
                        UPDATE orders
                        SET reserved_cents = reserved_cents - :release
                        WHERE id = :oid
                    """),
                    {"release": buyer_limit_cents * trade_qty, "oid": taker_id},
                )
            else:
                # maker is buyer (BUY maker order)
                db.execute(
                    text("""
                        UPDATE orders
                        SET reserved_cents = reserved_cents - :release
                        WHERE id = :oid
                    """),
                    {"release": buyer_limit_cents * trade_qty, "oid": maker["id"]},
                )

            # Seller: receive cash
            db.execute(
                text("""
                    UPDATE accounts
                    SET balance_cents = balance_cents + :earn,
                        updated_at = now()
                    WHERE user_id = :uid
                """),
                {"earn": fill_cents * trade_qty, "uid": seller_id},
            )

            # Shares transfer
            # Ensure positions row exists for buyer/seller
            db.execute(
                text("""
                    INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
                    VALUES (:uid, :mid, 0, 0, 0, 0, now())
                    ON CONFLICT (user_id, market_id) DO NOTHING
                """),
                [{"uid": buyer_id, "mid": payload.market_id}, {"uid": seller_id, "mid": payload.market_id}],
            )

            if payload.outcome == "YES":
                # buyer gains YES shares
                db.execute(
                    text("""
                        UPDATE positions
                        SET yes_shares = yes_shares + :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": trade_qty, "uid": buyer_id, "mid": payload.market_id},
                )
                # seller loses YES shares and unreserves sold shares
                db.execute(
                    text("""
                        UPDATE positions
                        SET yes_shares = yes_shares - :q,
                            yes_reserved = yes_reserved - :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": trade_qty, "uid": seller_id, "mid": payload.market_id},
                )
            else:
                db.execute(
                    text("""
                        UPDATE positions
                        SET no_shares = no_shares + :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": trade_qty, "uid": buyer_id, "mid": payload.market_id},
                )
                db.execute(
                    text("""
                        UPDATE positions
                        SET no_shares = no_shares - :q,
                            no_reserved = no_reserved - :q,
                            updated_at = now()
                        WHERE user_id = :uid AND market_id = :mid
                    """),
                    {"q": trade_qty, "uid": seller_id, "mid": payload.market_id},
                )

            # Update maker order qty/status
            new_maker_remaining = maker_remaining - trade_qty
            maker_status = "FILLED" if new_maker_remaining == 0 else "PARTIAL"
            db.execute(
                text("""
                    UPDATE orders
                    SET qty_remaining = :r,
                        status = :st
                    WHERE id = :oid
                """),
                {"r": new_maker_remaining, "st": maker_status, "oid": maker["id"]},
            )

            # Update taker remaining
            taker_remaining -= trade_qty
            taker_status = "FILLED" if taker_remaining == 0 else "PARTIAL"
            db.execute(
                text("""
                    UPDATE orders
                    SET qty_remaining = :r,
                        status = :st
                    WHERE id = :oid
                """),
                {"r": taker_remaining, "st": taker_status, "oid": taker_id},
            )

        # Return the final taker order row
        row = db.execute(
            text("""
                SELECT id::text, market_id::text, outcome, side, price_micros, qty, qty_remaining, status, created_at::text
                FROM orders
                WHERE id = :id
            """),
            {"id": taker_id},
        ).mappings().first()

        db.commit()
        return row

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Lock the order row
    o = db.execute(
        text("""
            SELECT id::text, user_id::text as user_id, side, price_micros, qty_remaining, status, reserved_cents
            FROM orders
            WHERE id = :oid
            FOR UPDATE
        """),
        {"oid": order_id},
    ).mappings().first()

    if not o:
        raise HTTPException(status_code=404, detail="order not found")
    if o["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="not your order")
    if o["status"] not in ("OPEN", "PARTIAL"):
        raise HTTPException(status_code=400, detail="order not cancelable")

    # Release reserved funds for remaining qty (BUY-only here)
    if o["side"] == "BUY":
        release = int(o["reserved_cents"])

        db.execute(
            text("""
                UPDATE accounts
                SET reserved_cents = reserved_cents - :delta,
                    updated_at = now()
                WHERE user_id = :uid
            """),
            {"delta": release, "uid": user["id"]},
        )

    # Mark canceled and zero remaining
    db.execute(
        text("""
            UPDATE orders
            SET status = 'CANCELED', qty_remaining = 0, reserved_cents = 0
            WHERE id = :oid
        """),
        {"oid": order_id},
    )

    db.commit()
    return {"ok": True}

@router.get("/markets/{market_id}/book")
def book(market_id: str, outcome: str, depth: int = 20, db: Session = Depends(get_db)):
    if outcome not in ("YES", "NO"):
        raise HTTPException(status_code=400, detail="outcome must be YES or NO")
    depth = max(1, min(depth, 100))

    bids = db.execute(
        text("""
            SELECT price_micros, qty_remaining, created_at::text
            FROM orders
            WHERE market_id = :mid AND outcome = :outcome
              AND side = 'BUY' AND status IN ('OPEN','PARTIAL')
            ORDER BY price_micros DESC, created_at ASC
            LIMIT :lim
        """),
        {"mid": market_id, "outcome": outcome, "lim": depth},
    ).mappings().all()

    asks = db.execute(
        text("""
            SELECT price_micros, qty_remaining, created_at::text
            FROM orders
            WHERE market_id = :mid AND outcome = :outcome
              AND side = 'SELL' AND status IN ('OPEN','PARTIAL')
            ORDER BY price_micros ASC, created_at ASC
            LIMIT :lim
        """),
        {"mid": market_id, "outcome": outcome, "lim": depth},
    ).mappings().all()

    return {"market_id": market_id, "outcome": outcome, "bids": bids, "asks": asks}

@router.get("/me/orders")
def my_orders(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id::text, market_id::text, outcome, side, price_micros, qty, qty_remaining, status, created_at::text
            FROM orders
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT 200
        """),
        {"uid": user["id"]},
    ).mappings().all()
    return {"orders": rows}

@router.get("/me/balance")
def my_balance(user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        text("""
            SELECT balance_cents, reserved_cents
            FROM accounts
            WHERE user_id = :uid
        """),
        {"uid": user["id"]},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=400, detail="account missing")
    return {
        "balance_cents": int(row["balance_cents"]),
        "reserved_cents": int(row["reserved_cents"]),
        "available_cents": int(row["balance_cents"] - row["reserved_cents"]),
    }
    

@router.get("/me/positions")
def my_positions(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT
                p.market_id::text AS market_id,
                m.slug AS market_slug,
                m.status AS market_status,
                p.yes_shares,
                p.no_shares,
                COALESCE(p.yes_reserved, 0) AS yes_reserved,
                COALESCE(p.no_reserved, 0) AS no_reserved,
                p.updated_at::text AS updated_at
            FROM positions p
            JOIN markets m ON m.id = p.market_id
            WHERE p.user_id = :uid
            ORDER BY p.updated_at DESC
            LIMIT 200
        """),
        {"uid": user["id"]},
    ).mappings().all()

    return {"positions": rows}