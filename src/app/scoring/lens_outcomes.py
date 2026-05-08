"""Compute realized outcomes for `LensSnapshot` rows whose horizon has elapsed.

For each (snapshot, horizon) pair where `as_of + horizon` is in the past:
- `return_pct`: ticker close at horizon vs close at `as_of`.
- `excess_vs_spy_pct`: ticker return minus SPY return over the same window.
- `direction_correct`: True if the lens's direction call matched what
  the price action actually did (with a small ±0.5% band for "neutral").
- `regime`: SPY 21-day trend at `as_of` (up / flat / down) — same regime
  classifier used by the strategy walk-forward (ADR 0007).

Idempotent: a row already in `lens_outcomes` for the same (snapshot, horizon)
is skipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensOutcome, LensSnapshot

HORIZONS: tuple[str, ...] = ("1d", "3d", "5d", "21d")
HORIZON_DAYS: dict[str, int] = {"1d": 1, "3d": 3, "5d": 5, "21d": 21}
NEUTRAL_BAND = 0.005  # ±0.5% — anything inside this counts as "neutral correct"


class MarketProvider(Protocol):
    """Minimal interface for return + regime lookup. The real implementation
    lives in `app.scoring.lens_market_adapter`; tests pass a stub.
    """

    def ticker_return(self, ticker: str, as_of: datetime, horizon: str) -> float | None: ...
    def spy_return(self, as_of: datetime, horizon: str) -> float | None: ...
    def regime_for(self, as_of: datetime) -> str: ...


def direction_correct(direction: str, return_pct: float) -> bool:
    if direction == "bullish":
        return return_pct > NEUTRAL_BAND
    if direction == "bearish":
        return return_pct < -NEUTRAL_BAND
    return abs(return_pct) <= NEUTRAL_BAND


def compute_lens_outcomes(
    session: Session,
    *,
    market: MarketProvider,
    max_age_days: int = 60,
) -> int:
    """Compute outcomes for all eligible snapshots. Returns the number of
    LensOutcome rows newly created.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    snapshots = (
        session.execute(
            select(LensSnapshot).where(LensSnapshot.as_of >= cutoff)
        )
        .scalars()
        .all()
    )

    n_created = 0
    for snap in snapshots:
        for horizon in HORIZONS:
            # Skip if snapshot is younger than the horizon
            elapsed = datetime.now(timezone.utc) - _ensure_aware(snap.as_of)
            if elapsed < timedelta(days=HORIZON_DAYS[horizon]):
                continue
            # Skip if outcome already computed
            existing = session.execute(
                select(LensOutcome).where(
                    LensOutcome.snapshot_id == snap.id,
                    LensOutcome.horizon == horizon,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue

            ticker_ret = market.ticker_return(snap.ticker, snap.as_of, horizon)
            if ticker_ret is None:
                continue
            spy_ret = market.spy_return(snap.as_of, horizon) or 0.0
            outcome = LensOutcome(
                snapshot_id=snap.id,
                horizon=horizon,
                return_pct=ticker_ret,
                excess_vs_spy_pct=ticker_ret - spy_ret,
                direction_correct=direction_correct(snap.direction, ticker_ret),
                regime=market.regime_for(snap.as_of),
            )
            session.add(outcome)
            n_created += 1

    if n_created:
        session.flush()
    return n_created


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite may return naive datetimes for DateTime(timezone=True) columns.
    Coerce to UTC-aware so timedelta math works.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = [
    "HORIZONS",
    "HORIZON_DAYS",
    "MarketProvider",
    "compute_lens_outcomes",
    "direction_correct",
]
