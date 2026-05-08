"""CreatorConsensus — third baseline strategy (ADR 0007 §2).

Thesis: when ≥2 *calibrated* creators (with `hit_rate_lower_ci > 0.55`)
issue same-direction trade calls on the same ticker within a 7-day window,
the consensus is worth researching. Long when the consensus is long, short
when it's short, equal-weighted, hold 5 trading days.

Hyperparameters (frozen at this version):
- lookback_days = 7
- min_calibrated_creators = 2
- credibility_threshold = 0.55 (matches the aggregator's
  CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD)
- holding_period_days = 5

Notes:
- Consensus is computed strictly on `recent_calls` (ExtractedCall), NOT on
  Claim. Calls carry an explicit direction; claims may not.
- `creator_scorecards` from ctx provides the credibility filter. A creator
  without a scorecard at as_of is treated as uncalibrated and dropped.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.strategies.base import (
    EvidenceRef,
    Strategy,
    StrategyContext,
    StrategyDecision,
)


class CreatorConsensus(Strategy):
    """Long/short when ≥2 calibrated creators agree on direction in last 7d."""

    name = "creator_consensus"
    version = "v0.1"

    def __init__(
        self,
        *,
        lookback_days: int = 7,
        min_calibrated_creators: int = 2,
        credibility_threshold: float = 0.55,
        holding_period_days: int = 5,
    ):
        if min_calibrated_creators < 1:
            raise ValueError("min_calibrated_creators must be >= 1")
        if not 0 <= credibility_threshold <= 1:
            raise ValueError("credibility_threshold must be in [0, 1]")
        self.lookback_days = lookback_days
        self.min_calibrated_creators = min_calibrated_creators
        self.credibility_threshold = credibility_threshold
        self.holding_period_days = holding_period_days

    @property
    def required_lookback_days(self) -> int:
        return max(self.lookback_days, 14)

    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]:
        # Identify calibrated creators at as_of.
        calibrated_creators: set[int] = set()
        for cid, scorecard in ctx.creator_scorecards.items():
            lci = scorecard.get("hit_rate_lower_ci") if isinstance(scorecard, dict) else getattr(scorecard, "hit_rate_lower_ci", None)
            if lci is not None and lci > self.credibility_threshold:
                calibrated_creators.add(int(cid))
        if len(calibrated_creators) < self.min_calibrated_creators:
            return []

        cutoff = ctx.as_of - timedelta(days=self.lookback_days)
        universe = set(ctx.universe)
        scoped = [
            c for c in ctx.recent_calls
            if c.get("ticker") in universe
            and c.get("creator_id") in calibrated_creators
            and c.get("direction") in ("long", "short")
            and c.get("posted_at") is not None
            and cutoff <= c["posted_at"] <= ctx.as_of
        ]
        if not scoped:
            return []

        # Group by (ticker, direction).
        by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for call in scoped:
            by_key[(call["ticker"], call["direction"])].append(call)

        # A ticker qualifies if any direction has ≥ min_calibrated_creators
        # *distinct* creators agreeing.
        qualifying: list[tuple[str, str, int, list[dict]]] = []
        for (ticker, direction), calls in by_key.items():
            creators = {c["creator_id"] for c in calls if c.get("creator_id") is not None}
            if len(creators) < self.min_calibrated_creators:
                continue
            # If the same ticker has ≥min agreeing in BOTH long and short, that's a
            # contradicting consensus — drop both. We check the inverse direction here.
            inverse = "short" if direction == "long" else "long"
            inv_calls = by_key.get((ticker, inverse), [])
            inv_creators = {c["creator_id"] for c in inv_calls if c.get("creator_id") is not None}
            if len(inv_creators) >= self.min_calibrated_creators:
                continue  # contradicted; skip
            qualifying.append((ticker, direction, len(creators), calls))

        if not qualifying:
            return []

        qualifying.sort(key=lambda x: x[2], reverse=True)
        weight = 1.0 / len(qualifying)
        decisions: list[StrategyDecision] = []
        for ticker, direction, n_creators, calls in qualifying:
            decisions.append(
                StrategyDecision(
                    ticker=ticker,
                    score=float(n_creators),
                    direction=direction,
                    weight=weight,
                    holding_period_days=self.holding_period_days,
                    evidence=[EvidenceRef(kind="call", id=c["id"]) for c in calls],
                    reasoning=(
                        f"{n_creators} calibrated creators agree on {direction} "
                        f"on {ticker} in last {self.lookback_days}d"
                    ),
                )
            )
        return decisions


__all__ = ["CreatorConsensus"]
