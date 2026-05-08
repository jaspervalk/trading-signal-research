"""Swing-based support/resistance + consolidation/breakout context.

Algorithm choice (transparent on purpose):
- Swing pivots are detected with a fixed `window` parameter: a bar is a
  swing high if its high is strictly greater than the previous and next
  `window` bars' highs. Symmetric for lows. Default window=5 ≈ 2 weeks.
- "Recent" levels are the swing pivots within the last `lookback` bars
  (default 252 ≈ 1 year). Historical pivots beyond that are dropped.
- Nearest support / resistance is the closest pivot below / above the
  current close. We do *not* cluster pivots — clustering adds tunable
  hyperparameters that hide behavior. The orchestrator can post-process
  if it wants.
- Consolidation/tightness is range over the last `tight_window` bars
  divided by the midprice. Below 5% on a 20-bar window is "tight"; tune
  via `is_in_tight_range_threshold`.

No leakage: every public function takes either an already-sliced
DataFrame (the caller is responsible) or accepts an `as_of` and slices.
The orchestrator slices once at the top.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from app.analysis.indicators import slice_at_or_before
from app.analysis.schema import LevelsPanel


def find_swing_highs(high: pd.Series, *, window: int = 5) -> list[tuple[pd.Timestamp, float]]:
    """Pivot highs strictly greater than `window` bars on each side.

    Returns a list of `(timestamp, price)` tuples in chronological order.
    Ignores the first/last `window` bars (we can't confirm the pivot at
    the edges without future data).
    """
    if len(high) < 2 * window + 1:
        return []
    out: list[tuple[pd.Timestamp, float]] = []
    h = high.values
    for i in range(window, len(h) - window):
        seg = h[i - window : i + window + 1]
        center = h[i]
        if pd.isna(center):
            continue
        # strictly greater than the others (avoids picking a flat plateau)
        if center > np.nanmax(np.delete(seg, window)):
            out.append((high.index[i], float(center)))
    return out


def find_swing_lows(low: pd.Series, *, window: int = 5) -> list[tuple[pd.Timestamp, float]]:
    if len(low) < 2 * window + 1:
        return []
    out: list[tuple[pd.Timestamp, float]] = []
    l = low.values
    for i in range(window, len(l) - window):
        seg = l[i - window : i + window + 1]
        center = l[i]
        if pd.isna(center):
            continue
        if center < np.nanmin(np.delete(seg, window)):
            out.append((low.index[i], float(center)))
    return out


def nearest_above(price: float, candidates: list[float]) -> float | None:
    above = [c for c in candidates if c > price]
    return min(above) if above else None


def nearest_below(price: float, candidates: list[float]) -> float | None:
    below = [c for c in candidates if c < price]
    return max(below) if below else None


def consolidation_range_pct(
    high: pd.Series, low: pd.Series, *, n: int = 20
) -> float | None:
    """Range over the last n bars / midprice. None when too few bars."""
    if len(high) < n or len(low) < n:
        return None
    h_max = float(high.iloc[-n:].max())
    l_min = float(low.iloc[-n:].min())
    mid = (h_max + l_min) / 2
    if mid == 0:
        return None
    return (h_max - l_min) / mid


def pullback_pct_from_recent_high(
    close: pd.Series, *, lookback: int = 63
) -> float | None:
    """How far back from the recent N-bar high are we?

    Positive = pulled back, e.g. 0.08 means current close is 8% below the
    63-bar high. None when too few bars.
    """
    if len(close) < lookback:
        return None
    window = close.iloc[-lookback:]
    recent_high = float(window.max())
    last = float(close.iloc[-1])
    if recent_high == 0:
        return None
    return (recent_high - last) / recent_high


def breakout_distance_pct(
    close: pd.Series, *, lookback: int = 63
) -> float | None:
    """Signed distance from the recent N-bar high.

    Positive = price is above the recent N-bar high (a breakout).
    Negative = price is below it (the high is overhead resistance).
    """
    if len(close) < lookback + 1:
        return None
    # Exclude the last bar from the lookback to avoid trivial 0% when the
    # current bar IS the recent high.
    window = close.iloc[-(lookback + 1) : -1]
    if window.empty:
        return None
    recent_high = float(window.max())
    last = float(close.iloc[-1])
    if recent_high == 0:
        return None
    return (last - recent_high) / recent_high


def build_levels_panel(
    df: pd.DataFrame,
    *,
    as_of: datetime,
    pivot_window: int = 5,
    lookback: int = 252,
    tight_window: int = 20,
    is_in_tight_range_threshold: float = 0.05,
    keep_top_n_levels: int = 5,
) -> LevelsPanel:
    """Compose the LevelsPanel from a price DataFrame."""
    df = slice_at_or_before(df, as_of)
    if df.empty:
        return LevelsPanel()

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    last_close = float(close.iloc[-1])

    # Restrict pivots to the lookback window. We compute pivots over the
    # full sliced frame, then filter to those within `lookback` bars.
    swings_hi = find_swing_highs(high, window=pivot_window)
    swings_lo = find_swing_lows(low, window=pivot_window)

    if lookback < len(close):
        cutoff_idx = close.index[-lookback]
        swings_hi = [(t, p) for (t, p) in swings_hi if t >= cutoff_idx]
        swings_lo = [(t, p) for (t, p) in swings_lo if t >= cutoff_idx]

    # Top N most-recent each side, ordered for the panel.
    recent_highs = sorted(
        [p for _, p in swings_hi[-keep_top_n_levels:]], reverse=True
    )
    recent_lows = sorted([p for _, p in swings_lo[-keep_top_n_levels:]])

    nearest_res = nearest_above(last_close, [p for _, p in swings_hi])
    nearest_sup = nearest_below(last_close, [p for _, p in swings_lo])

    res_dist_pct = (
        (nearest_res - last_close) / last_close
        if nearest_res is not None and last_close > 0
        else None
    )
    sup_dist_pct = (
        (last_close - nearest_sup) / last_close
        if nearest_sup is not None and last_close > 0
        else None
    )

    recent_high_63 = float(close.iloc[-min(63, len(close)) :].max())
    recent_low_63 = float(close.iloc[-min(63, len(close)) :].min())

    consolidation = consolidation_range_pct(high, low, n=tight_window)
    is_tight = consolidation is not None and consolidation <= is_in_tight_range_threshold
    breakout = breakout_distance_pct(close, lookback=63)
    pullback = pullback_pct_from_recent_high(close, lookback=63)

    # Base low/high: the lowest swing low and highest swing high in the
    # most-recent `tight_window * 3` bars (~3 months for tight_window=20).
    base_window = min(tight_window * 3, len(df))
    base_low = float(low.iloc[-base_window:].min()) if base_window > 0 else None
    base_high = float(high.iloc[-base_window:].max()) if base_window > 0 else None

    return LevelsPanel(
        swing_highs=recent_highs,
        swing_lows=recent_lows,
        nearest_resistance=nearest_res,
        nearest_support=nearest_sup,
        nearest_resistance_distance_pct=res_dist_pct,
        nearest_support_distance_pct=sup_dist_pct,
        recent_high_63d=recent_high_63,
        recent_low_63d=recent_low_63,
        pullback_pct_from_recent_high=pullback,
        consolidation_range_pct=consolidation,
        is_in_tight_range=is_tight,
        breakout_distance_pct=breakout,
        base_low=base_low,
        base_high=base_high,
    )


__all__ = [
    "breakout_distance_pct",
    "build_levels_panel",
    "consolidation_range_pct",
    "find_swing_highs",
    "find_swing_lows",
    "nearest_above",
    "nearest_below",
    "pullback_pct_from_recent_high",
]
