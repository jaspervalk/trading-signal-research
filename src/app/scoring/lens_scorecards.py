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


@dataclass
class LensScorecard:
    lens_name: str
    horizon: str
    n: int
    hit_rate: float
    hit_rate_lo: float
    hit_rate_hi: float
    avg_excess_vs_spy: float
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

        by_regime = _bucket(pairs, key=lambda p: p[1].regime)
        by_conviction = _bucket(pairs, key=lambda p: p[0].conviction)
        by_direction = _bucket(pairs, key=lambda p: p[0].direction)

        cards.append(
            LensScorecard(
                lens_name=lens_name,
                horizon=horizon,
                n=n,
                hit_rate=hits / n if n else 0.0,
                hit_rate_lo=lo,
                hit_rate_hi=hi,
                avg_excess_vs_spy=round(avg_excess, 4),
                by_regime=by_regime,
                by_conviction=by_conviction,
                by_direction=by_direction,
            )
        )
    cards.sort(key=lambda c: c.lens_name)
    return cards


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
