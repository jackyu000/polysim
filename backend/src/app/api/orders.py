from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
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
from app.schemas.orders import OrderCreateIn, OrderOut
from app.services.exchange import cancel_order as cancel_order_service
from app.services.exchange import create_order as create_order_service
from app.services.exchange import get_order_book

router = APIRouter(prefix="/api", tags=["orders"])


def raise_http_from_domain_error(exc: Exception) -> None:
    if isinstance(exc, MarketNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (MarketNotOpen, InvalidOrder, OrderNotCancelable)):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (InsufficientBalance, InsufficientInventory)):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ForbiddenOrderAccess):
        raise HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, OrderNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.post("/orders", response_model=OrderOut)
def create_order(payload: OrderCreateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        row = create_order_service(
            db,
            user_id=user["id"],
            market_id=payload.market_id,
            outcome=payload.outcome,
            side=payload.side,
            price_micros=payload.price_micros,
            qty=payload.qty,
        )
        db.commit()
        return row
    except Exception as exc:
        db.rollback()
        raise_http_from_domain_error(exc)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        result = cancel_order_service(db, user_id=user["id"], order_id=order_id)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise_http_from_domain_error(exc)


@router.get("/markets/{market_id}/book")
def book(market_id: str, outcome: str, depth: int = 20, db: Session = Depends(get_db)):
    try:
        return get_order_book(db, market_id=market_id, outcome=outcome, depth=depth)
    except Exception as exc:
        raise_http_from_domain_error(exc)


@router.get("/me/orders")
def my_orders(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT id::text, market_id::text, outcome, side, price_micros, qty, qty_remaining, status, created_at::text
            FROM orders
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT 200
            """
        ),
        {"uid": user["id"]},
    ).mappings().all()
    return {"orders": rows}


@router.get("/me/balance")
def my_balance(user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        text(
            """
            SELECT balance_cents, reserved_cents
            FROM accounts
            WHERE user_id = :uid
            """
        ),
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
        text(
            """
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
            """
        ),
        {"uid": user["id"]},
    ).mappings().all()

    return {"positions": rows}
