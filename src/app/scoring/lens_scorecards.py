"""Aggregate `LensSnapshot` x `LensOutcome` into per-lens accuracy scorecards.

Mirrors `CreatorScorecard` style: hit rate with Wilson 95% CI, plus regime
and conviction breakdowns. Used by the CLI / API surface; later phases
inject this into the judge's prompt for differentiated weighting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensOutcome, LensSnapshot
from app.scoring.metrics import wilson_ci


DIRECTION_SIGN: dict[str, int] = {"bullish": 1, "bearish": -1, "neutral": 0}


@dataclass
class LensScorecard:
    lens_name: str
    horizon: str
    n: int
    n_ticker_days: int
    """Distinct (ticker, session date) pairs behind `n`.

    `n` counts snapshots, and snapshots cluster hard: one Deep run emits four
    lens rows on the same ticker at the same instant, and a session of
    research emits many runs on a handful of names. Those observations share
    almost all of their price action, so `n` overstates the independent
    evidence and the Wilson CI computed from it is narrower than the truth.
    Read `n_ticker_days` as the honest denominator.
    """
    hit_rate: float
    hit_rate_lo: float
    hit_rate_hi: float
    avg_excess_vs_spy: float
    """Mean excess return of the *tickers this lens looked at* — NOT a
    quality measure. Direction-blind: a lens that called a name bearish is
    credited with the rally it warned against. Kept because it describes the
    sample; use `avg_directional_excess` to judge the lens.
    """
    avg_directional_excess: float | None
    """Mean excess return of *acting on this lens's call*: `+excess` for a
    bullish read, `−excess` for a bearish one. Neutral reads carry no
    directional P&L and are excluded (see `n_directional`). None when the
    lens made no directional calls.
    """
    n_directional: int = 0
    by_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    by_conviction: dict[str, dict[str, float]] = field(default_factory=dict)
    by_direction: dict[str, dict[str, float]] = field(default_factory=dict)


def compute_lens_scorecards(
    session: Session,
    *,
    lookback_days: int = 90,
    horizon: str = "5d",
) -> list[LensScorecard]:
    """One LensScorecard per lens_name. Snapshots without matching outcome
    rows at the requested horizon are excluded.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = session.execute(
        select(LensSnapshot, LensOutcome)
        .join(LensOutcome, LensOutcome.snapshot_id == LensSnapshot.id)
        .where(LensOutcome.horizon == horizon)
        .where(LensSnapshot.as_of >= cutoff)
    ).all()

    by_lens: dict[str, list[tuple[LensSnapshot, LensOutcome]]] = {}
    for snap, outc in rows:
        by_lens.setdefault(snap.lens_name, []).append((snap, outc))

    cards: list[LensScorecard] = []
    for lens_name, pairs in by_lens.items():
        n = len(pairs)
        hits = sum(1 for _, o in pairs if o.direction_correct)
        lo, hi = wilson_ci(hits, n)
        avg_excess = sum(o.excess_vs_spy_pct for _, o in pairs) / n if n else 0.0

        # Sign the excess by what the lens actually said. Without this, a
        # perma-bearish lens reports the same positive "excess" as a bullish
        # one on the same rallying ticker.
        directional = [
            DIRECTION_SIGN[s.direction] * o.excess_vs_spy_pct
            for s, o in pairs
            if DIRECTION_SIGN.get(s.direction, 0) != 0
        ]
        n_directional = len(directional)
        avg_directional = (
            sum(directional) / n_directional if n_directional else None
        )

        ticker_days = {
            (s.ticker, _ensure_aware(s.as_of).date()) for s, _ in pairs
        }

        by_regime = _bucket(pairs, key=lambda p: p[1].regime)
        by_conviction = _bucket(pairs, key=lambda p: p[0].conviction)
        by_direction = _bucket(pairs, key=lambda p: p[0].direction)

        cards.append(
            LensScorecard(
                lens_name=lens_name,
                horizon=horizon,
                n=n,
                n_ticker_days=len(ticker_days),
                hit_rate=hits / n if n else 0.0,
                hit_rate_lo=lo,
                hit_rate_hi=hi,
                avg_excess_vs_spy=round(avg_excess, 4),
                avg_directional_excess=(
                    round(avg_directional, 4) if avg_directional is not None else None
                ),
                n_directional=n_directional,
                by_regime=by_regime,
                by_conviction=by_conviction,
                by_direction=by_direction,
            )
        )
    cards.sort(key=lambda c: c.lens_name)
    return cards


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes for `DateTime(timezone=True)`."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bucket(
    pairs: list[tuple[LensSnapshot, LensOutcome]],
    key: Callable[[tuple[LensSnapshot, LensOutcome]], str],
) -> dict[str, dict[str, float]]:
    """Group pairs by `key(pair)`; return {bucket_name: {n, hits, hit_rate, lo, hi}}."""
    buckets: dict[str, list[tuple[LensSnapshot, LensOutcome]]] = {}
    for p in pairs:
        buckets.setdefault(key(p), []).append(p)
    out: dict[str, dict[str, float]] = {}
    for k, group in buckets.items():
        n = len(group)
        hits = sum(1 for _, o in group if o.direction_correct)
        lo, hi = wilson_ci(hits, n)
        out[k] = {
            "n": n,
            "hits": hits,
            "hit_rate": hits / n if n else 0.0,
            "hit_rate_lo": lo,
            "hit_rate_hi": hi,
        }
    return out


__all__ = ["LensScorecard", "compute_lens_scorecards"]
