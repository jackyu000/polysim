from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.schemas.markets import MarketDetailOut, MarketPositionOut, MarketsPageOut, TradesPageOut

router = APIRouter(prefix="/api", tags=["markets"])


def encode_cursor(created_at: str, market_id: str) -> str:
    payload = json.dumps({"created_at": created_at, "id": market_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc

    created_at = payload.get("created_at")
    market_id = payload.get("id")
    if not created_at or not market_id:
        raise HTTPException(status_code=400, detail="invalid cursor")
    return {"created_at": created_at, "id": market_id}


@router.get("/markets", response_model=MarketsPageOut)
def list_markets(
    status: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    params: dict[str, Any] = {"limit_plus_one": limit + 1}
    where_clauses: list[str] = []

    if status is not None:
        where_clauses.append("status = :status")
        params["status"] = status

    if cursor:
        cursor_values = decode_cursor(cursor)
        where_clauses.append(
            """
            (
                created_at < CAST(:cursor_created_at AS timestamptz)
                OR (
                    created_at = CAST(:cursor_created_at AS timestamptz)
                    AND id < CAST(:cursor_id AS uuid)
                )
            )
            """
        )
        params["cursor_created_at"] = cursor_values["created_at"]
        params["cursor_id"] = cursor_values["id"]

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = text(
        f"""
        SELECT id::text AS id,
               slug,
               question,
               status,
               resolves_at::text AS resolves_at,
               resolved_outcome,
               resolved_at::text AS resolved_at,
               created_at::text AS created_at
        FROM markets
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT :limit_plus_one
        """
    )

    rows = db.execute(query, params).mappings().all()

    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last["created_at"], last["id"])
        rows = rows[:limit]

    markets = [
        {
            "id": row["id"],
            "slug": row["slug"],
            "question": row["question"],
            "status": row["status"],
            "resolves_at": row["resolves_at"],
            "resolved_outcome": row["resolved_outcome"],
            "resolved_at": row["resolved_at"],
        }
        for row in rows
    ]
    return {"markets": markets, "next_cursor": next_cursor}


@router.get("/markets/{market_id}", response_model=MarketDetailOut)
def get_market(market_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            """
            SELECT id::text AS id,
                   slug,
                   question,
                   status,
                   resolves_at::text AS resolves_at,
                   resolved_outcome,
                   resolved_at::text AS resolved_at,
                   created_at::text AS created_at
            FROM markets
            WHERE id = :mid
            """
        ),
        {"mid": market_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="market not found")
    return dict(row)


@router.get("/markets/{market_id}/trades", response_model=TradesPageOut)
def get_market_trades(market_id: str, limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = db.execute(
        text(
            """
            SELECT id::text AS id,
                   maker_order_id::text AS maker_order_id,
                   taker_order_id::text AS taker_order_id,
                   price_micros,
                   qty,
                   ts::text AS ts
            FROM trades
            WHERE market_id = :mid
            ORDER BY ts DESC
            LIMIT :limit
            """
        ),
        {"mid": market_id, "limit": limit},
    ).mappings().all()
    return {"trades": rows}


@router.get("/markets/{market_id}/position", response_model=MarketPositionOut)
def get_market_position(
    market_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    market = db.execute(
        text("SELECT 1 FROM markets WHERE id = :mid"),
        {"mid": market_id},
    ).first()
    if not market:
        raise HTTPException(status_code=404, detail="market not found")

    row = db.execute(
        text(
            """
            SELECT market_id::text AS market_id,
                   yes_shares,
                   no_shares,
                   COALESCE(yes_reserved, 0) AS yes_reserved,
                   COALESCE(no_reserved, 0) AS no_reserved,
                   updated_at::text AS updated_at
            FROM positions
            WHERE user_id = :uid AND market_id = :mid
            """
        ),
        {"uid": user["id"], "mid": market_id},
    ).mappings().first()

    if not row:
        return {
            "market_id": market_id,
            "yes_shares": 0,
            "no_shares": 0,
            "yes_reserved": 0,
            "no_reserved": 0,
            "updated_at": None,
        }

    return dict(row)
