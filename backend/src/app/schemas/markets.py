from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MarketSummaryOut(BaseModel):
    id: str
    slug: str
    question: str
    status: str
    resolves_at: str | None
    resolved_outcome: str | None
    resolved_at: str | None


class MarketDetailOut(MarketSummaryOut):
    created_at: str


class MarketsPageOut(BaseModel):
    markets: list[MarketSummaryOut]
    next_cursor: str | None


class TradeOut(BaseModel):
    id: str
    maker_order_id: str
    taker_order_id: str
    price_micros: int
    qty: int
    ts: str


class TradesPageOut(BaseModel):
    trades: list[TradeOut]


class MarketPositionOut(BaseModel):
    market_id: str
    yes_shares: int
    no_shares: int
    yes_reserved: int
    no_reserved: int
    updated_at: str | None


class ResolveMarketIn(BaseModel):
    outcome: Literal["YES", "NO"]


class ResolveMarketOut(BaseModel):
    market_id: str
    status: str
    resolved_outcome: str
    resolved_at: str | None
    canceled_orders: int
    settled_positions: int
    total_payout_cents: int
