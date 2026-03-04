from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.main import app


def _base_database_url() -> str:
    return os.getenv(
        "TEST_DATABASE_URL",
        os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/polysim"),
    )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    base_url = make_url(_base_database_url())
    admin_url = base_url.set(database="postgres")
    database_name = f"polysim_test_{secrets.token_hex(4)}"
    target_url = base_url.set(database=database_name)

    admin_engine = create_engine(admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
    except SQLAlchemyError as exc:
        pytest.skip(f"postgres unavailable for integration tests: {exc}")

    alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", target_url.render_as_string(hide_password=False))
    command.upgrade(alembic_cfg, "head")

    yield target_url.render_as_string(hide_password=False)

    with admin_engine.connect() as conn:
        conn.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :database_name
                  AND pid <> pg_backend_pid()
                """
            ),
            {"database_name": database_name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def db_engine(test_database_url: str):
    engine = create_engine(test_database_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def SessionTesting(db_engine):
    return sessionmaker(bind=db_engine, autocommit=False, autoflush=False, class_=Session)


@pytest.fixture(autouse=True)
def clean_database(db_engine):
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                TRUNCATE TABLE bridge_orders, external_quotes, external_market_map,
                               trades, orders, positions, accounts, sessions, markets, users
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield


@pytest.fixture()
def db_session(SessionTesting):
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(SessionTesting):
    def override_get_db():
        db = SessionTesting()
        try:
            yield db
        finally:
            db.close()

    auth_state: dict[str, dict | None] = {"user": None}

    def override_get_current_user():
        if auth_state["user"] is None:
            raise HTTPException(status_code=401, detail="test user not configured")
        return auth_state["user"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    test_client = TestClient(app)
    try:
        yield test_client, auth_state
    finally:
        app.dependency_overrides.clear()


def create_user(db: Session, *, email: str, balance_cents: int = 10_000) -> dict:
    user_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash)
            VALUES (:id, :email, :password_hash)
            """
        ),
        {"id": user_id, "email": email, "password_hash": "test-hash"},
    )
    db.execute(
        text(
            """
            INSERT INTO accounts (user_id, balance_cents, reserved_cents, updated_at)
            VALUES (:uid, :balance_cents, 0, now())
            """
        ),
        {"uid": user_id, "balance_cents": balance_cents},
    )
    return {"id": user_id, "email": email}


def create_market(
    db: Session,
    *,
    slug: str,
    question: str,
    status: str = "OPEN",
    created_at: str | None = None,
) -> str:
    market_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO markets (id, slug, question, status, resolves_at, resolved_outcome, resolved_at, created_at)
            VALUES (
                :id,
                :slug,
                :question,
                :status,
                NULL,
                NULL,
                NULL,
                COALESCE(CAST(:created_at AS timestamptz), now())
            )
            """
        ),
        {
            "id": market_id,
            "slug": slug,
            "question": question,
            "status": status,
            "created_at": created_at,
        },
    )
    return market_id


def ensure_position(
    db: Session,
    *,
    user_id: str,
    market_id: str,
    yes_shares: int = 0,
    no_shares: int = 0,
    yes_reserved: int = 0,
    no_reserved: int = 0,
):
    db.execute(
        text(
            """
            INSERT INTO positions (user_id, market_id, yes_shares, no_shares, yes_reserved, no_reserved, updated_at)
            VALUES (:uid, :mid, :yes_shares, :no_shares, :yes_reserved, :no_reserved, now())
            ON CONFLICT (user_id, market_id) DO UPDATE
            SET yes_shares = EXCLUDED.yes_shares,
                no_shares = EXCLUDED.no_shares,
                yes_reserved = EXCLUDED.yes_reserved,
                no_reserved = EXCLUDED.no_reserved,
                updated_at = now()
            """
        ),
        {
            "uid": user_id,
            "mid": market_id,
            "yes_shares": yes_shares,
            "no_shares": no_shares,
            "yes_reserved": yes_reserved,
            "no_reserved": no_reserved,
        },
    )
