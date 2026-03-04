from __future__ import annotations

from sqlalchemy import text

from app.services.exchange import cancel_order, create_order
from tests.helpers import create_market, create_user, ensure_position


def test_buy_matches_sell_and_transfers_cash_and_shares(db_session):
    buyer = create_user(db_session, email="buyer@example.com")
    seller = create_user(db_session, email="seller@example.com")
    market_id = create_market(db_session, slug="market-1", question="Will it rain?")
    ensure_position(db_session, user_id=seller["id"], market_id=market_id, yes_shares=5)

    create_order(
        db_session,
        user_id=seller["id"],
        market_id=market_id,
        outcome="YES",
        side="SELL",
        price_micros=500_000,
        qty=5,
    )
    order = create_order(
        db_session,
        user_id=buyer["id"],
        market_id=market_id,
        outcome="YES",
        side="BUY",
        price_micros=600_000,
        qty=5,
    )
    db_session.commit()

    assert order["status"] == "FILLED"
    trade_count = db_session.execute(text("SELECT count(*) FROM trades")).scalar_one()
    assert trade_count == 1

    buyer_account = db_session.execute(
        text("SELECT balance_cents, reserved_cents FROM accounts WHERE user_id = :uid"),
        {"uid": buyer["id"]},
    ).mappings().one()
    seller_account = db_session.execute(
        text("SELECT balance_cents, reserved_cents FROM accounts WHERE user_id = :uid"),
        {"uid": seller["id"]},
    ).mappings().one()
    buyer_position = db_session.execute(
        text("SELECT yes_shares, yes_reserved FROM positions WHERE user_id = :uid AND market_id = :mid"),
        {"uid": buyer["id"], "mid": market_id},
    ).mappings().one()
    seller_position = db_session.execute(
        text("SELECT yes_shares, yes_reserved FROM positions WHERE user_id = :uid AND market_id = :mid"),
        {"uid": seller["id"], "mid": market_id},
    ).mappings().one()

    assert buyer_account["balance_cents"] == 9_750
    assert buyer_account["reserved_cents"] == 0
    assert seller_account["balance_cents"] == 10_250
    assert seller_position["yes_shares"] == 0
    assert seller_position["yes_reserved"] == 0
    assert buyer_position["yes_shares"] == 5


def test_partial_fill_then_cancel_releases_remaining_buy_reserve(db_session):
    buyer = create_user(db_session, email="buyer2@example.com")
    seller = create_user(db_session, email="seller2@example.com")
    market_id = create_market(db_session, slug="market-2", question="Will it snow?")
    ensure_position(db_session, user_id=seller["id"], market_id=market_id, yes_shares=2)

    create_order(
        db_session,
        user_id=seller["id"],
        market_id=market_id,
        outcome="YES",
        side="SELL",
        price_micros=500_000,
        qty=2,
    )
    buy_order = create_order(
        db_session,
        user_id=buyer["id"],
        market_id=market_id,
        outcome="YES",
        side="BUY",
        price_micros=600_000,
        qty=5,
    )

    partial = db_session.execute(
        text("SELECT reserved_cents, qty_remaining, status FROM orders WHERE id = :oid"),
        {"oid": buy_order["id"]},
    ).mappings().one()
    assert partial["status"] == "PARTIAL"
    assert partial["qty_remaining"] == 3
    assert partial["reserved_cents"] == 180

    cancel_order(db_session, user_id=buyer["id"], order_id=buy_order["id"])
    db_session.commit()

    buyer_account = db_session.execute(
        text("SELECT balance_cents, reserved_cents FROM accounts WHERE user_id = :uid"),
        {"uid": buyer["id"]},
    ).mappings().one()
    canceled = db_session.execute(
        text("SELECT status, qty_remaining, reserved_cents FROM orders WHERE id = :oid"),
        {"oid": buy_order["id"]},
    ).mappings().one()

    assert buyer_account["balance_cents"] == 9_900
    assert buyer_account["reserved_cents"] == 0
    assert canceled["status"] == "CANCELED"
    assert canceled["qty_remaining"] == 0
    assert canceled["reserved_cents"] == 0


def test_self_trade_is_prevented(db_session):
    trader = create_user(db_session, email="self@example.com")
    market_id = create_market(db_session, slug="market-3", question="Will the price rise?")
    ensure_position(db_session, user_id=trader["id"], market_id=market_id, yes_shares=3)

    create_order(
        db_session,
        user_id=trader["id"],
        market_id=market_id,
        outcome="YES",
        side="SELL",
        price_micros=500_000,
        qty=3,
    )
    buy_order = create_order(
        db_session,
        user_id=trader["id"],
        market_id=market_id,
        outcome="YES",
        side="BUY",
        price_micros=600_000,
        qty=3,
    )
    db_session.commit()

    trade_count = db_session.execute(text("SELECT count(*) FROM trades")).scalar_one()
    buy_state = db_session.execute(
        text("SELECT status, qty_remaining, reserved_cents FROM orders WHERE id = :oid"),
        {"oid": buy_order["id"]},
    ).mappings().one()

    assert trade_count == 0
    assert buy_state["status"] == "OPEN"
    assert buy_state["qty_remaining"] == 3
    assert buy_state["reserved_cents"] == 180
