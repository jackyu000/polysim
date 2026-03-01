from pydantic import BaseModel, Field
from typing import Literal

Outcome = Literal["YES", "NO"]
Side = Literal["BUY", "SELL"]

class OrderCreateIn(BaseModel):
    market_id: str
    outcome: Outcome
    side: Side
    price_micros: int = Field(ge=0, le=1_000_000)
    qty: int = Field(ge=1, le=1_000_000)

class OrderOut(BaseModel):
    id: str
    market_id: str
    outcome: Outcome
    side: Side
    price_micros: int
    qty: int
    qty_remaining: int
    status: str
    created_at: str