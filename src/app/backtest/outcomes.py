"""Outcome metrics per (call, horizon).

Convention:
  - `return_pct` is the simple price return from `fill_price` to the
    horizon's exit price, signed in the direction of the trade
    (long: +1*delta, short: -1*delta).
  - `mfe` / `mae` are intra-window max favourable / max adverse excursion,
    again signed in the trade's direction.
  - `hit_target` / `hit_stop` are only meaningful if the call carries
    those prices.
  - Costs are applied as `return_pct = gross_return_pct - cost_bps/10000`.
    cost_bps is interpreted as a *round-trip* cost (entry + exit combined).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.models import DIRECTION_LONG, ExtractedCall


@dataclass
class OutcomeMetrics:
    exit_at: datetime | None
    exit_price: float | None
    gross_return_pct: float | None
    return_pct: float | None
    mfe: float | None
    mae: float | None
    hit_target: bool | None
    hit_stop: bool | None
    benchmark_return_pct: float | None
    excess_return_pct: float | None


def _to_utc_ts(dt: datetime) -> pd.Timestamp:
    """Convert any datetime to a tz-aware UTC pandas Timestamp."""
    if dt.tzinfo is None:
        return pd.Timestamp(dt).tz_localize("UTC")
    return pd.Timestamp(dt).tz_convert("UTC")


def _slice_window(
    daily: pd.DataFrame, start: datetime, end: datetime
) -> pd.DataFrame:
    lo = _to_utc_ts(start)
    hi = _to_utc_ts(end)
    return daily[(daily.index >= lo) & (daily.index <= hi)]


def _signed_pct(fill: float, value: float, direction: str) -> float:
    raw = (value - fill) / fill
    return raw if direction == DIRECTION_LONG else -raw


def compute_outcome(
    call: ExtractedCall,
    *,
    fill_price: float,
    activation_at: datetime,
    daily_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    horizon_end_at: datetime,
    cost_bps: int = 10,
) -> OutcomeMetrics:
    """Compute outcome metrics. `daily_bars` covers ticker; `benchmark_bars`
    covers the SPY benchmark over the same dates."""
    win = _slice_window(daily_bars, activation_at, horizon_end_at)
    if win.empty:
        return OutcomeMetrics(
            exit_at=None, exit_price=None,
            gross_return_pct=None, return_pct=None,
            mfe=None, mae=None,
            hit_target=None, hit_stop=None,
            benchmark_return_pct=None, excess_return_pct=None,
        )

    direction = call.direction or DIRECTION_LONG
    last = win.iloc[-1]
    exit_price = float(last["Close"])
    exit_at = last.name.to_pydatetime().astimezone(UTC) if hasattr(last.name, "to_pydatetime") else activation_at

    gross = _signed_pct(fill_price, exit_price, direction)
    net = gross - (cost_bps / 10000.0)

    # MFE / MAE — extremes during the window, signed in trade direction.
    if direction == DIRECTION_LONG:
        favourable_extreme = float(win["High"].max())
        adverse_extreme = float(win["Low"].min())
    else:
        favourable_extreme = float(win["Low"].min())
        adverse_extreme = float(win["High"].max())
    mfe = _signed_pct(fill_price, favourable_extreme, direction)
    mae = _signed_pct(fill_price, adverse_extreme, direction)

    hit_target: bool | None = None
    if call.target_price is not None:
        if direction == DIRECTION_LONG:
            hit_target = bool((win["High"] >= call.target_price).any())
        else:
            hit_target = bool((win["Low"] <= call.target_price).any())

    hit_stop: bool | None = None
    if call.stop_price is not None:
        if direction == DIRECTION_LONG:
            hit_stop = bool((win["Low"] <= call.stop_price).any())
        else:
            hit_stop = bool((win["High"] >= call.stop_price).any())

    bench_ret: float | None = None
    excess: float | None = None
    if not benchmark_bars.empty:
        bw = _slice_window(benchmark_bars, activation_at, horizon_end_at)
        if not bw.empty:
            entry_close = float(bw["Close"].iloc[0])
            exit_close = float(bw["Close"].iloc[-1])
            bench_ret = (exit_close - entry_close) / entry_close
            excess = net - bench_ret

    return OutcomeMetrics(
        exit_at=exit_at,
        exit_price=exit_price,
        gross_return_pct=gross,
        return_pct=net,
        mfe=mfe,
        mae=mae,
        hit_target=hit_target,
        hit_stop=hit_stop,
        benchmark_return_pct=bench_ret,
        excess_return_pct=excess,
    )
