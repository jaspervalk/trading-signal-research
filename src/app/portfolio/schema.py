"""Pydantic models for the portfolio ledger.

`Position` is the pure ledger output — no market data. `PositionView` extends
it with quote-derived fields so the API surface stays flat for the web client.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDES = (SIDE_BUY, SIDE_SELL)


class TradeRecord(BaseModel):
    """One fill. Mirrors the `portfolio_trades` row."""

    id: int | None = None
    ticker: str
    side: str
    quantity: float
    price_per_share: float
    currency: str = "USD"
    fees: float = 0.0
    traded_at: datetime
    eur_amount: float | None = None
    note: str | None = None


class TradeIn(BaseModel):
    """Payload for creating a trade."""

    ticker: str = Field(min_length=1, max_length=16)
    side: str
    quantity: float = Field(gt=0)
    price_per_share: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    fees: float = Field(default=0.0, ge=0)
    traded_at: datetime
    eur_amount: float | None = Field(default=None, gt=0)
    note: str | None = None


class TradePatch(BaseModel):
    """Payload for editing a trade. Every field optional."""

    ticker: str | None = Field(default=None, min_length=1, max_length=16)
    side: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    price_per_share: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fees: float | None = Field(default=None, ge=0)
    traded_at: datetime | None = None
    eur_amount: float | None = Field(default=None, gt=0)
    note: str | None = None


class Position(BaseModel):
    """Derived holding for one ticker. Pure ledger output."""

    ticker: str
    currency: str
    quantity: float
    avg_cost: float | None
    cost_basis: float
    realized_pnl: float
    eur_avg_cost: float | None = None
    eur_cost_basis: float | None = None
    eur_realized_pnl: float | None = None
    first_traded_at: datetime
    last_traded_at: datetime
    trade_count: int

    @property
    def is_open(self) -> bool:
        return self.quantity > 0


class PositionView(Position):
    """A position plus live market data."""

    last_price: float | None = None
    previous_close: float | None = None
    market_value: float | None = None
    market_value_eur: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pct: float | None = None
    day_change_pct: float | None = None


class PortfolioView(BaseModel):
    """Everything the portfolio page renders."""

    open_positions: list[PositionView]
    closed_positions: list[PositionView]
    total_market_value: float | None = None
    total_market_value_eur: float | None = None
    total_unrealized_pnl: float | None = None
    total_realized_pnl: float = 0.0
    eur_usd_rate: float | None = None
    quote_errors: list[str] = Field(default_factory=list)
    as_of: datetime
