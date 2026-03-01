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
    # Phase 0 Step 3: BUY-only to keep it simple
    if payload.side != "BUY":
        raise HTTPException(status_code=400, detail="SELL not enabled yet (Step 3 is BUY-only)")

    # Ensure market exists and is OPEN
    m = db.execute(
        text("SELECT status FROM markets WHERE id = :id"),
        {"id": payload.market_id},
    ).mappings().first()
    if not m:
        raise HTTPException(status_code=404, detail="market not found")
    if m["status"] != "OPEN":
        raise HTTPException(status_code=400, detail="market not open")

    price_cents = price_micros_to_cents(payload.price_micros)
    cost_cents = price_cents * payload.qty

    # Reserve funds atomically
    # We lock account row and check available >= cost
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

    order_id = uuid.uuid4()

    db.execute(
        text("""
            INSERT INTO orders (id, market_id, user_id, outcome, side, price_micros, qty, qty_remaining, status)
            VALUES (:id, :mid, :uid, :outcome, :side, :price, :qty, :qty, 'OPEN')
        """),
        {
            "id": order_id,
            "mid": payload.market_id,
            "uid": user["id"],
            "outcome": payload.outcome,
            "side": payload.side,
            "price": payload.price_micros,
            "qty": payload.qty,
        },
    )

    row = db.execute(
        text("""
            SELECT id::text, market_id::text, outcome, side, price_micros, qty, qty_remaining, status, created_at::text
            FROM orders
            WHERE id = :id
        """),
        {"id": order_id},
    ).mappings().first()

    db.commit()
    return row

@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Lock the order row
    o = db.execute(
        text("""
            SELECT id::text, user_id::text as user_id, side, price_micros, qty_remaining, status
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
        price_cents = price_micros_to_cents(int(o["price_micros"]))
        release = price_cents * int(o["qty_remaining"])

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
            SET status = 'CANCELED', qty_remaining = 0
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