"""Stock screener — funnel from broad universe to research candidates.

The screener is intentionally **not** a ranking model. It filters a wide
universe (S&P 500 + Nasdaq 100 by default) down to 20-40 candidates the
user can hand to the existing `tsr research <TICKER>` view (or Deep mode via the dashboard).

Design rules (ADR 0005 framing applies):

- No composite scores.
- Show metrics + which filters passed; the user decides what to research.
- Creator coverage is **additive, never penalising**. A ticker with zero
  YouTube coverage is not ranked lower — finding those tickers is the
  whole point.
"""

from app.screener.schema import (
    FilterResult,
    ScreenConfig,
    ScreenResult,
    ScreenRow,
    TickerMetrics,
)

__all__ = [
    "FilterResult",
    "ScreenConfig",
    "ScreenResult",
    "ScreenRow",
    "TickerMetrics",
]
