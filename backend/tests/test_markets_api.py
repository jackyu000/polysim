from __future__ import annotations

from sqlalchemy import text

from app.services.exchange import create_order
from tests.helpers import create_market, create_user, ensure_position


def test_market_list_paginates_and_detail_returns_market(client, db_session):
    first_id = create_market(
        db_session,
        slug="older-market",
        question="Older market?",
        created_at="2026-01-01T00:00:00+00:00",
    )
    second_id = create_market(
        db_session,
        slug="newer-market",
        question="Newer market?",
        created_at="2026-01-02T00:00:00+00:00",
    )
    db_session.commit()

    test_client, _ = client
    first_page = test_client.get("/api/markets", params={"limit": 1, "status": "OPEN"})
    assert first_page.status_code == 200
    payload = first_page.json()
    assert payload["markets"][0]["id"] == second_id
    assert payload["next_cursor"]

    second_page = test_client.get("/api/markets", params={"limit": 1, "cursor": payload["next_cursor"]})
    assert second_page.status_code == 200
    assert second_page.json()["markets"][0]["id"] == first_id

    detail = test_client.get(f"/api/markets/{first_id}")
    assert detail.status_code == 200
    assert detail.json()["slug"] == "older-market"


def test_market_trades_and_zero_position_endpoint(client, db_session):
    buyer = create_user(db_session, email="buyer3@example.com")
    seller = create_user(db_session, email="seller3@example.com")
    market_id = create_market(db_session, slug="trade-market", question="Trade market?")
    ensure_position(db_session, user_id=seller["id"], market_id=market_id, yes_shares=1)

    create_order(
        db_session,
        user_id=seller["id"],
        market_id=market_id,
        outcome="YES",
        side="SELL",
        price_micros=450_000,
        qty=1,
    )
    create_order(
        db_session,
        user_id=buyer["id"],
        market_id=market_id,
        outcome="YES",
        side="BUY",
        price_micros=500_000,
        qty=1,
    )
    another_market = create_market(db_session, slug="empty-position", question="No position?")
    db_session.commit()

    test_client, auth_state = client
    trades = test_client.get(f"/api/markets/{market_id}/trades", params={"limit": 10})
    assert trades.status_code == 200
    assert len(trades.json()["trades"]) == 1

    auth_state["user"] = buyer
    position = test_client.get(f"/api/markets/{another_market}/position")
    assert position.status_code == 200
    assert position.json()["yes_shares"] == 0
    assert position.json()["no_shares"] == 0


def test_admin_resolution_endpoint_resolves_market_and_pays_winner(client, db_session, monkeypatch):
    admin = create_user(db_session, email="admin@example.com")
    winner = create_user(db_session, email="winner@example.com")
    loser = create_user(db_session, email="loser@example.com")
    market_id = create_market(db_session, slug="resolve-market", question="Resolve market?")

    ensure_position(db_session, user_id=winner["id"], market_id=market_id, yes_shares=4)
    ensure_position(db_session, user_id=loser["id"], market_id=market_id, no_shares=3)

    create_order(
        db_session,
        user_id=loser["id"],
        market_id=market_id,
        outcome="NO",
        side="SELL",
        price_micros=400_000,
        qty=1,
    )
    db_session.commit()

    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    test_client, auth_state = client
    auth_state["user"] = admin

    response = test_client.post(f"/api/admin/markets/{market_id}/resolve", json={"outcome": "YES"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "CLOSED"
    assert payload["resolved_outcome"] == "YES"
    assert payload["total_payout_cents"] == 400

    market = db_session.execute(
        text("SELECT status, resolved_outcome, resolved_at IS NOT NULL AS has_resolved_at FROM markets WHERE id = :mid"),
        {"mid": market_id},
    ).mappings().one()
    winner_account = db_session.execute(
        text("SELECT balance_cents FROM accounts WHERE user_id = :uid"),
        {"uid": winner["id"]},
    ).mappings().one()
    positions = db_session.execute(
        text("SELECT yes_shares, no_shares, yes_reserved, no_reserved FROM positions WHERE market_id = :mid ORDER BY user_id"),
        {"mid": market_id},
    ).mappings().all()

    assert market["status"] == "CLOSED"
    assert market["resolved_outcome"] == "YES"
    assert market["has_resolved_at"] is True
    assert winner_account["balance_cents"] == 10_400
    assert all(row["yes_shares"] == 0 and row["no_shares"] == 0 for row in positions)
