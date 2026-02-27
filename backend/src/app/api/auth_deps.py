from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from jose import JWTError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import decode_access_token


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid token (no sub)")

    row = db.execute(
        text("SELECT id::text, email FROM users WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=401, detail="user not found")

    return row  # {"id": "...", "email": "..."}