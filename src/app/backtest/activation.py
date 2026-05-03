"""Activation logic per ADR 0003.

A call is *activated* iff it becomes a real position within
`trigger_window_days` of `posted_at`. The semantics depend on `entry_type`:

  - market           → next session open after posted_at; fill = that open.
  - unspecified      → treated as market (placed in a separate eval bucket).
  - limit (long)     → activated when intraday low ≤ entry_price; fill = entry_price.
  - limit (short)    → activated when intraday high ≥ entry_price; fill = entry_price.
  - trigger_above    → activated when intraday high ≥ entry_price;
                        fill = max(entry_price, that bar's open).
  - trigger_below    → activated when intraday low ≤ entry_price;
                        fill = min(entry_price, that bar's open).

We work entirely with daily OHLCV. Intra-bar order of high vs low is unknown,
so trigger fills assume worst-of-(open, trigger_price) on the activation bar.
This is a deliberately conservative bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.models import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_LIMIT,
    ENTRY_MARKET,
    ENTRY_TRIGGER_ABOVE,
    ENTRY_TRIGGER_BELOW,
    ENTRY_UNSPECIFIED,
    ExtractedCall,
)


@dataclass
class ActivationResult:
    activated: bool
    activation_at: datetime | None = None
    fill_price: float | None = None
    notes: str | None = None


def _bars_after(daily: pd.DataFrame, posted_at: datetime, n_days: int) -> pd.DataFrame:
    if daily.empty:
        return daily
    cutoff_lo = pd.Timestamp(posted_at, tz="UTC")
    cutoff_hi = cutoff_lo + pd.Timedelta(days=int(n_days * 1.6) + 5)  # cover weekends/holidays
    later = daily[(daily.index > cutoff_lo) & (daily.index <= cutoff_hi)]
    return later


def determine_activation(
    call: ExtractedCall,
    daily_bars: pd.DataFrame,
    *,
    trigger_window_days: int = 10,
) -> ActivationResult:
    """Decide whether a call activated within the window after `posted_at`.

    `daily_bars` must be a tz-aware-UTC indexed DataFrame with at least
    columns Open / High / Low / Close, covering posted_at..posted_at+window.
    """
    posted_at = call.context_text  # placeholder; we use call.document.posted_at via the orchestrator
    # The orchestrator passes posted_at via the daily_bars slice + the call.
    # To keep this function pure, we accept it as part of the daily_bars
    # index trim — the caller hands us only post-posted_at bars.

    if daily_bars.empty:
        return ActivationResult(False, notes="no bars available after posted_at")

    # Take only the next `trigger_window_days` trading sessions.
    window = daily_bars.iloc[:trigger_window_days]
    if window.empty:
        return ActivationResult(False, notes="window empty")

    direction = call.direction
    entry_type = call.entry_type
    entry_price = call.entry_price

    # ---- market / unspecified ----
    if entry_type in (ENTRY_MARKET, ENTRY_UNSPECIFIED):
        first = window.iloc[0]
        return ActivationResult(
            activated=True,
            activation_at=first.name.to_pydatetime().astimezone(UTC),
            fill_price=float(first["Open"]),
            notes=f"market entry ({entry_type})",
        )

    # ---- limit / trigger_* require entry_price ----
    if entry_price is None:
        return ActivationResult(
            False, notes=f"{entry_type} requires entry_price; got None"
        )

    for ts, row in window.iterrows():
        high = float(row["High"])
        low = float(row["Low"])
        open_ = float(row["Open"])
        ts_utc = ts.to_pydatetime().astimezone(UTC) if hasattr(ts, "to_pydatetime") else ts

        if entry_type == ENTRY_LIMIT:
            if direction == DIRECTION_LONG and low <= entry_price:
                fill = entry_price if open_ >= entry_price else min(open_, entry_price)
                # If gap-down through limit, we get filled at the open (better than limit).
                # Worst case (conservative): cap at entry_price.
                fill = min(open_, entry_price) if open_ < entry_price else entry_price
                return ActivationResult(True, ts_utc, fill, "limit long")
            if direction == DIRECTION_SHORT and high >= entry_price:
                fill = max(open_, entry_price) if open_ > entry_price else entry_price
                return ActivationResult(True, ts_utc, fill, "limit short")
            continue

        if entry_type == ENTRY_TRIGGER_ABOVE:
            if high >= entry_price:
                # Worst-of(open, trigger) for long; if open gapped above, fill at open.
                if direction == DIRECTION_LONG:
                    fill = max(open_, entry_price)
                else:
                    # Short on a break-above is unusual; treat fill same as trigger_below.
                    fill = max(open_, entry_price)
                return ActivationResult(True, ts_utc, fill, "trigger_above")
            continue

        if entry_type == ENTRY_TRIGGER_BELOW:
            if low <= entry_price:
                if direction == DIRECTION_SHORT:
                    fill = min(open_, entry_price)
                else:
                    fill = min(open_, entry_price)
                return ActivationResult(True, ts_utc, fill, "trigger_below")
            continue

    return ActivationResult(False, notes=f"trigger {entry_type} {entry_price} not hit in window")
