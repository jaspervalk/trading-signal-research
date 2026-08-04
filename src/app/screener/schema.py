"""Schema for the stock screener.

Every value on `TickerMetrics` is optional — yfinance routinely returns
`None` for one field while the rest are populated. The pipeline must
treat partial data as the common case, not an error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TickerMetrics(BaseModel):
    """Raw metrics fetched per ticker. Every field is nullable."""

    ticker: str
    sector: Optional[str] = None
    industry: Optional[str] = None

    # Valuation
    market_cap: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    ev_to_ebitda: Optional[float] = None

    # Growth
    revenue_growth_yoy: Optional[float] = None  # fraction, e.g. 0.18 = 18%
    earnings_growth_yoy: Optional[float] = None
    revenue_growth_qoq: Optional[float] = None  # latest Q vs prior Q

    # Quality
    gross_margin: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None  # raw debt/equity, not the ×100 variant

    # Technical
    last_close: Optional[float] = None
    sma_200: Optional[float] = None
    pct_vs_200d: Optional[float] = None  # (last - sma200) / sma200
    rs_vs_spy_3mo: Optional[float] = None  # ticker_return_63d - spy_return_63d

    # Catalyst (informational, never filters)
    days_to_earnings: Optional[int] = None

    # Creator coverage — additive only, never used to rank or filter
    creator_mentions: int = 0
    avg_creator_confidence: Optional[float] = None

    # Diagnostics
    data_quality: list[str] = Field(default_factory=list)  # missing-field tags


class FilterResult(BaseModel):
    """Outcome of one filter against one ticker."""

    name: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)  # short human-readable bullets


class ScreenRow(BaseModel):
    """One ticker's full screen result."""

    metrics: TickerMetrics
    filters: list[FilterResult]
    flags: list[str] = Field(default_factory=list)  # e.g. "cheap_for_a_reason"
    error: Optional[str] = None

    @property
    def ticker(self) -> str:
        return self.metrics.ticker

    @property
    def filters_passed(self) -> list[str]:
        return [f.name for f in self.filters if f.passed]


class ScreenConfig(BaseModel):
    """User-tunable thresholds. Defaults match the ADR-locked spec."""

    # Which filters to enable. None = "all of them".
    enabled_filters: Optional[list[str]] = None

    # Valuation thresholds
    max_forward_pe: float = 25.0
    max_peg: float = 1.5
    max_ev_ebitda: float = 15.0

    # Growth thresholds
    min_revenue_growth: float = 0.15  # 15% YoY
    require_earnings_positive: bool = True

    # Quality thresholds
    min_gross_margin: float = 0.40
    min_roe: float = 0.15
    max_debt_to_equity: float = 1.0

    # Technical thresholds
    max_extension_above_200d: float = 0.30  # reject if > +30% above 200d SMA
    min_rs_vs_spy_3mo: float = 0.0  # positive = outperforming SPY

    # Catalyst
    earnings_proximity_days: int = 30  # flag, not filter

    # Optional sector filter
    sector: Optional[str] = None


class ScreenResult(BaseModel):
    """Top-level result of a screening run."""

    as_of: datetime
    config: ScreenConfig
    universe_size: int
    n_completed: int
    n_errors: int
    rows: list[ScreenRow]

    @property
    def passing(self) -> list[ScreenRow]:
        """Rows that passed at least one filter."""
        return [r for r in self.rows if r.filters_passed and r.error is None]


__all__ = [
    "FilterResult",
    "ScreenConfig",
    "ScreenResult",
    "ScreenRow",
    "TickerMetrics",
]
