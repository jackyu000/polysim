from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.auth_deps import get_current_user
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    new_refresh_token,
    hash_refresh_token,
    verify_refresh_token,
    refresh_expires_at,
)
from app.schemas.auth import RegisterIn, LoginIn, TokenOut, RefreshIn, MeOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)):
    # Ensure email unique
    existing = db.execute(
        text("SELECT 1 FROM users WHERE email = :email"),
        {"email": payload.email},
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")

    user_id = uuid.uuid4()
    pw_hash = hash_password(payload.password)

    db.execute(
        text("""
            INSERT INTO users (id, email, password_hash)
            VALUES (:id, :email, :ph)
        """),
        {"id": user_id, "email": payload.email, "ph": pw_hash},
    )

    # Create account with starting balance (Phase 0: 100.00)
    db.execute(
        text("""
            INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
            VALUES (:uid, :bal, 0, now())
        """),
        {"uid": user_id, "bal": 100_00},
    )

    # Create refresh session
    refresh = new_refresh_token()
    refresh_hash = hash_refresh_token(refresh)
    sess_id = uuid.uuid4()

    db.execute(
        text("""
            INSERT INTO sessions (id, user_id, refresh_token_hash, created_at, expires_at, revoked_at, user_agent, ip)
            VALUES (:id, :uid, :rth, now(), :exp, NULL, :ua, :ip)
        """),
        {
            "id": sess_id,
            "uid": user_id,
            "rth": refresh_hash,
            "exp": refresh_expires_at(),
            "ua": request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
        },
    )

    db.commit()

    access = create_access_token(sub=str(user_id))
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT id::text as id, password_hash FROM users WHERE email = :email"),
        {"email": payload.email},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="invalid credentials")

    if not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")

    refresh = new_refresh_token()
    refresh_hash = hash_refresh_token(refresh)
    sess_id = uuid.uuid4()

    db.execute(
        text("""
            INSERT INTO sessions (id, user_id, refresh_token_hash, created_at, expires_at, revoked_at, user_agent, ip)
            VALUES (:id, :uid, :rth, now(), :exp, NULL, :ua, :ip)
        """),
        {
            "id": sess_id,
            "uid": row["id"],
            "rth": refresh_hash,
            "exp": refresh_expires_at(),
            "ua": request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
        },
    )
    db.commit()

    access = create_access_token(sub=row["id"])
    return TokenOut(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, request: Request, db: Session = Depends(get_db)):
    # Find a non-revoked session that matches this refresh token.
    # Since we store only the hash, we must scan candidate sessions.
    # Phase 0 approach: scan active (not expired/revoked) sessions for the user later; for now scan all active sessions.
    candidates = db.execute(
        text("""
            SELECT id::text as id, user_id::text as user_id, refresh_token_hash
            FROM sessions
            WHERE revoked_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC
            LIMIT 50
        """)
    ).mappings().all()

    match = None
    for s in candidates:
        if verify_refresh_token(payload.refresh_token, s["refresh_token_hash"]):
            match = s
            break

    if not match:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    # Revoke old session (rotation)
    db.execute(
        text("UPDATE sessions SET revoked_at = now() WHERE id = :id"),
        {"id": match["id"]},
    )

    # Create new session
    new_r = new_refresh_token()
    new_r_hash = hash_refresh_token(new_r)
    new_sess_id = uuid.uuid4()

    db.execute(
        text("""
            INSERT INTO sessions (id, user_id, refresh_token_hash, created_at, expires_at, revoked_at, user_agent, ip)
            VALUES (:id, :uid, :rth, now(), :exp, NULL, :ua, :ip)
        """),
        {
            "id": new_sess_id,
            "uid": match["user_id"],
            "rth": new_r_hash,
            "exp": refresh_expires_at(),
            "ua": request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
        },
    )
    db.commit()

    access = create_access_token(sub=match["user_id"])
    return TokenOut(access_token=access, refresh_token=new_r)


@router.post("/logout")
def logout(payload: RefreshIn, db: Session = Depends(get_db)):
    # Revoke whichever active session matches this refresh token
    candidates = db.execute(
        text("""
            SELECT id::text as id, refresh_token_hash
            FROM sessions
            WHERE revoked_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC
            LIMIT 50
        """)
    ).mappings().all()

    for s in candidates:
        if verify_refresh_token(payload.refresh_token, s["refresh_token_hash"]):
            db.execute(text("UPDATE sessions SET revoked_at = now() WHERE id = :id"), {"id": s["id"]})
            db.commit()
            return {"ok": True}

    raise HTTPException(status_code=401, detail="invalid refresh token")


@router.get("/me", response_model=MeOut)
def me(user=Depends(get_current_user)):
    return MeOut(id=user["id"], email=user["email"])