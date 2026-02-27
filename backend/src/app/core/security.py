from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError
from passlib.hash import argon2

# In real life you load this from env; for Phase 0 you can hardcode, but DON'T commit a real secret later.
JWT_SECRET = "dev-secret-change-me"
JWT_ALG = "HS256"
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 30


def hash_password(password: str) -> str:
    return argon2.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return argon2.verify(password, password_hash)


def create_access_token(*, sub: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_MINUTES)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    if payload.get("type") != "access":
        raise JWTError("wrong token type")
    return payload


def new_refresh_token() -> str:
    # Opaque random token (NOT a JWT). We store ONLY a hash of it in DB.
    return secrets.token_urlsafe(48)


def refresh_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS)


def hash_refresh_token(token: str) -> str:
    # Using argon2 is fine for Phase 0; later you might use HMAC+pepper for speed.
    return argon2.hash(token)


def verify_refresh_token(token: str, token_hash: str) -> bool:
    return argon2.verify(token, token_hash)