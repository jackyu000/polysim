from __future__ import annotations

import importlib
import uuid

from sqlalchemy import text

from app.services.polymarket_targets import get_quote_targets
from tests.helpers import create_market, create_user


def test_quote_targets_prefer_recent_trades_and_respect_limit(db_session):
    market_old = create_market(
        db_session,
        slug="old-market",
        question="Old?",
        created_at="2026-01-01T00:00:00+00:00",
    )
    market_order = create_market(
        db_session,
        slug="order-market",
        question="Order?",
        created_at="2026-01-02T00:00:00+00:00",
    )
    market_trade = create_market(
        db_session,
        slug="trade-market",
        question="Trade?",
        created_at="2026-01-03T00:00:00+00:00",
    )

    db_session.execute(
        text(
            """
        INSERT INTO external_market_map (market_id, venue, yes_token_id, no_token_id, updated_at)
        VALUES
          (:old_mid, 'POLYMARKET', 'yes-old', 'no-old', now()),
          (:order_mid, 'POLYMARKET', 'yes-order', 'no-order', now()),
          (:trade_mid, 'POLYMARKET', 'yes-trade', 'no-trade', now())
        """
        ),
        {"old_mid": market_old, "order_mid": market_order, "trade_mid": market_trade},
    )

    user = create_user(db_session, email="activity@example.com")
    db_session.execute(
        text(
            """
        INSERT INTO orders (id, market_id, user_id, outcome, side, price_micros, qty, qty_remaining, status, reserved_cents, created_at)
        VALUES
          (:order_id, :order_mid, :uid, 'YES', 'BUY', 500000, 1, 1, 'OPEN', 50, '2026-01-05T00:00:00+00:00'),
          (:trade_order_id, :trade_mid, :uid, 'YES', 'BUY', 500000, 1, 0, 'FILLED', 0, '2026-01-04T00:00:00+00:00')
        """
        ),
        {
            "order_id": str(uuid.uuid4()),
            "trade_order_id": str(uuid.uuid4()),
            "order_mid": market_order,
            "trade_mid": market_trade,
            "uid": user["id"],
        },
    )
    db_session.execute(
        text(
            """
        INSERT INTO trades (id, market_id, maker_order_id, taker_order_id, price_micros, qty, ts)
        VALUES (:trade_id, :trade_mid, :maker_order_id, :taker_order_id, 500000, 1, '2026-01-06T00:00:00+00:00')
        """
        ),
        {
            "trade_id": str(uuid.uuid4()),
            "trade_mid": market_trade,
            "maker_order_id": str(uuid.uuid4()),
            "taker_order_id": str(uuid.uuid4()),
        },
    )
    db_session.commit()

    targets = get_quote_targets(db_session, limit=2)
    assert [target["market_id"] for target in targets] == [market_trade, market_order]


def test_bridge_reconcile_side_reuses_and_replaces(monkeypatch):
    monkeypatch.setenv("BRIDGE_BOT_USER_ID", "00000000-0000-0000-0000-000000000001")
    bridge = importlib.import_module("app.workers.bridge_polymarket_mm")
    bridge = importlib.reload(bridge)

    monkeypatch.setattr(bridge, "get_tracked_order", lambda *args, **kwargs: "tracked-order")
    monkeypatch.setattr(
        bridge,
        "get_order_price_and_status",
        lambda *args, **kwargs: (100_000, "OPEN", bridge.SIZE_QTY),
    )

    called = {"cancel": 0, "create": 0, "remember": 0, "delete": 0}
    monkeypatch.setattr(
        bridge,
        "cancel_order_service",
        lambda *args, **kwargs: called.__setitem__("cancel", called["cancel"] + 1),
    )
    monkeypatch.setattr(
        bridge,
        "create_order_service",
        lambda *args, **kwargs: called.__setitem__("create", called["create"] + 1) or {"id": "new-order"},
    )
    monkeypatch.setattr(
        bridge,
        "remember_bridge_order",
        lambda *args, **kwargs: called.__setitem__("remember", called["remember"] + 1),
    )
    monkeypatch.setattr(
        bridge,
        "delete_bridge_order",
        lambda *args, **kwargs: called.__setitem__("delete", called["delete"] + 1),
    )

    reused, reason = bridge.reconcile_side(
        object(),
        market_id="market",
        outcome="YES",
        side="BUY",
        target_price=105_000,
    )
    assert reused == "reused"
    assert reason is None
    assert called == {"cancel": 0, "create": 0, "remember": 0, "delete": 0}

    replaced, reason = bridge.reconcile_side(
        object(),
        market_id="market",
        outcome="YES",
        side="BUY",
        target_price=120_000,
    )
    assert replaced == "replaced"
    assert reason is None
    assert called["cancel"] == 1
    assert called["create"] == 1
    assert called["remember"] == 1
    assert called["delete"] == 1
