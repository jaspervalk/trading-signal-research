"""Pure-function scoring primitives. No DB, no IO.

Kept tiny and well-tested: every aggregate metric in CreatorScorecard
should ultimately route through one of these.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def wilson_ci(successes: int, n: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion.

    Use this — not the naive `p ± 1.96 * sqrt(p(1-p)/n)` interval —
    because the naive form collapses to (p, p) at p=0 or p=1, and is
    badly biased at small N. Wilson degrades gracefully.
    """
    if n <= 0:
        return (0.0, 1.0)
    if confidence == 0.95:
        z = 1.959964
    elif confidence == 0.99:
        z = 2.575829
    else:
        # Approximation good enough for the few values we'd actually use.
        z = 1.959964
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) / n) + (z2 / (4 * n * n)))) / denom
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    return lo, hi


def hit_rate_with_ci(
    returns: Sequence[float | None], *, threshold: float = 0.0
) -> tuple[float | None, float | None, float | None, int]:
    """(hit_rate, lower_ci, upper_ci, n) — None when no observations."""
    obs = [r for r in returns if r is not None]
    n = len(obs)
    if n == 0:
        return (None, None, None, 0)
    hits = sum(1 for r in obs if r > threshold)
    p = hits / n
    lo, hi = wilson_ci(hits, n)
    return (p, lo, hi, n)


def expectancy(returns: Sequence[float | None]) -> float | None:
    obs = [r for r in returns if r is not None]
    return sum(obs) / len(obs) if obs else None


def std(returns: Sequence[float | None], *, ddof: int = 1) -> float | None:
    obs = [r for r in returns if r is not None]
    if len(obs) <= ddof:
        return None
    mean = sum(obs) / len(obs)
    var = sum((x - mean) ** 2 for x in obs) / (len(obs) - ddof)
    return math.sqrt(var)


def sharpe_like(returns: Sequence[float | None]) -> float | None:
    """Mean / std of per-call returns. NOT annualised — internal comparator only."""
    s = std(returns)
    if s is None or s == 0:
        return None
    m = expectancy(returns)
    return None if m is None else m / s


def median(returns: Sequence[float | None]) -> float | None:
    obs = sorted(r for r in returns if r is not None)
    n = len(obs)
    if n == 0:
        return None
    if n % 2:
        return obs[n // 2]
    return 0.5 * (obs[n // 2 - 1] + obs[n // 2])
