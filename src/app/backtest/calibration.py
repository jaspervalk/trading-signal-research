"""Calibration of strategy scores against realized outcomes (ADR 0007 §5).

Take a list of `(score, hit)` pairs (where `hit ∈ {0, 1}` is the realized
binary outcome — return > 0 = win), bucket by score deciles, and report:
- mean predicted score per bucket
- realized hit rate per bucket
- Brier score (calibration + refinement, lower is better)
- reliability-diagram slope/intercept

Pure functions on numbers. No DB, no IO. Built so the CLI's report can
include calibration when the strategy emits a meaningful `score`.

Per ADR 0007: "A strategy whose calibration is off by >2 deciles in any
bucket is flagged in the report and surfaced in the dashboard's
'Methodology' panel."
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class CalibrationBucket:
    """One decile of the score distribution."""

    decile: int  # 1..n_buckets
    n: int
    mean_score: float
    realized_hit_rate: float
    score_min: float
    score_max: float


@dataclass
class CalibrationReport:
    n_buckets: int
    n_observations: int
    brier_score: float | None
    reliability_slope: float | None
    reliability_intercept: float | None
    miscalibration_max: float | None  # max |predicted - realized| across buckets
    miscalibrated_buckets: list[int] = field(default_factory=list)
    buckets: list[CalibrationBucket] = field(default_factory=list)


def calibrate(
    scores: list[float],
    hits: list[int],
    *,
    n_buckets: int = 10,
    miscalibration_threshold: float = 0.20,
) -> CalibrationReport:
    """Bucket `(scores, hits)` into deciles + return a CalibrationReport.

    `scores` should be in `[0, 1]` for true Brier interpretation. Strategies
    that emit raw scores in arbitrary ranges should min-max normalize before
    calling. Returns an empty report when n < n_buckets.
    """
    if len(scores) != len(hits):
        raise ValueError("scores and hits must have the same length")
    n = len(scores)
    if n < n_buckets:
        return CalibrationReport(
            n_buckets=n_buckets,
            n_observations=n,
            brier_score=None,
            reliability_slope=None,
            reliability_intercept=None,
            miscalibration_max=None,
        )

    arr_scores = np.asarray(scores, dtype=float)
    arr_hits = np.asarray(hits, dtype=float)

    # Quantile-bucket so each bucket has roughly n/n_buckets observations.
    quantiles = np.quantile(arr_scores, np.linspace(0, 1, n_buckets + 1))
    quantiles[0] -= 1e-9  # ensure the leftmost edge is strict
    quantiles[-1] += 1e-9
    buckets: list[CalibrationBucket] = []
    miscal_buckets: list[int] = []
    miscal_max = 0.0

    for i in range(n_buckets):
        mask = (arr_scores > quantiles[i]) & (arr_scores <= quantiles[i + 1])
        n_in_bucket = int(mask.sum())
        if n_in_bucket == 0:
            continue
        mean_score = float(arr_scores[mask].mean())
        hit_rate = float(arr_hits[mask].mean())
        gap = abs(mean_score - hit_rate)
        miscal_max = max(miscal_max, gap)
        if gap > miscalibration_threshold:
            miscal_buckets.append(i + 1)
        buckets.append(
            CalibrationBucket(
                decile=i + 1,
                n=n_in_bucket,
                mean_score=mean_score,
                realized_hit_rate=hit_rate,
                score_min=float(quantiles[i]),
                score_max=float(quantiles[i + 1]),
            )
        )

    brier = float(np.mean((arr_scores - arr_hits) ** 2))

    slope, intercept = _reliability_fit(buckets)

    return CalibrationReport(
        n_buckets=n_buckets,
        n_observations=n,
        brier_score=brier,
        reliability_slope=slope,
        reliability_intercept=intercept,
        miscalibration_max=miscal_max,
        miscalibrated_buckets=miscal_buckets,
        buckets=buckets,
    )


def _reliability_fit(buckets: list[CalibrationBucket]) -> tuple[float | None, float | None]:
    """Linear fit of realized_hit_rate ~ mean_score across buckets.

    Perfect calibration → slope=1, intercept=0. Returns (None, None) when
    there are fewer than 2 buckets or the fit is degenerate.
    """
    if len(buckets) < 2:
        return None, None
    xs = np.array([b.mean_score for b in buckets], dtype=float)
    ys = np.array([b.realized_hit_rate for b in buckets], dtype=float)
    if xs.std() == 0:
        return None, None
    slope, intercept = np.polyfit(xs, ys, 1)
    if math.isnan(slope) or math.isnan(intercept):
        return None, None
    return float(slope), float(intercept)


__all__ = ["CalibrationBucket", "CalibrationReport", "calibrate"]
