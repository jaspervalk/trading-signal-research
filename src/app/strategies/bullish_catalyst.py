"""BullishCatalystAggregator — second baseline strategy (ADR 0007 §2).

Thesis: when ≥2 distinct creators independently raise a *catalyst* claim
(`claim_type='catalyst'`) on the same ticker within a 14-day window, and
the claims are factual or opinion (not speculation/hype), there's a higher
chance the market is reacting to a real event we should research. Long the
qualifying tickers, equal-weighted, hold 5 trading days.

Hyperparameters (frozen at this version):
- lookback_days = 14
- min_distinct_creators = 2
- claim_classes_allowed = {'factual', 'opinion'}
- holding_period_days = 5

Like MentionMomentum, this strategy:
- never queries the DB directly — uses ctx.recent_claims only
- never uses datetime.now() — only ctx.as_of
- emits long-only decisions (V1 simplification)
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


_BULLISH_POLARITIES = {"bullish"}


class BullishCatalystAggregator(Strategy):
    """Long tickers with multi-creator bullish catalyst claims in last 14d."""

    name = "bullish_catalyst_aggregator"
    version = "v0.1"

    def __init__(
        self,
        *,
        lookback_days: int = 14,
        min_distinct_creators: int = 2,
        claim_classes_allowed: tuple[str, ...] = ("factual", "opinion"),
        holding_period_days: int = 5,
    ):
        if min_distinct_creators < 1:
            raise ValueError("min_distinct_creators must be >= 1")
        self.lookback_days = lookback_days
        self.min_distinct_creators = min_distinct_creators
        self.claim_classes_allowed = set(claim_classes_allowed)
        self.holding_period_days = holding_period_days

    @property
    def required_lookback_days(self) -> int:
        return max(self.lookback_days, 21)

    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]:
        cutoff = ctx.as_of - timedelta(days=self.lookback_days)
        universe = set(ctx.universe)
        scoped = [
            c for c in ctx.recent_claims
            if c.get("ticker") in universe
            and c.get("claim_type") == "catalyst"
            and c.get("claim_class") in self.claim_classes_allowed
            and c.get("polarity") in _BULLISH_POLARITIES
            and c.get("posted_at") is not None
            and cutoff <= c["posted_at"] <= ctx.as_of
        ]
        if not scoped:
            return []

        by_ticker: dict[str, list[dict]] = defaultdict(list)
        for c in scoped:
            by_ticker[c["ticker"]].append(c)

        qualifying: list[tuple[str, int, list[dict]]] = []
        for ticker, claims in by_ticker.items():
            creators = {c["creator_id"] for c in claims if c.get("creator_id") is not None}
            if len(creators) < self.min_distinct_creators:
                continue
            qualifying.append((ticker, len(creators), claims))

        if not qualifying:
            return []

        qualifying.sort(key=lambda x: x[1], reverse=True)
        weight = 1.0 / len(qualifying)
        decisions: list[StrategyDecision] = []
        for ticker, n_creators, claims in qualifying:
            decisions.append(
                StrategyDecision(
                    ticker=ticker,
                    score=float(n_creators),
                    direction="long",
                    weight=weight,
                    holding_period_days=self.holding_period_days,
                    evidence=[EvidenceRef(kind="claim", id=c["id"]) for c in claims],
                    reasoning=(
                        f"{n_creators} distinct creators raised bullish catalysts "
                        f"on {ticker} in last {self.lookback_days}d "
                        f"({len(claims)} claim(s))"
                    ),
                )
            )
        return decisions


__all__ = ["BullishCatalystAggregator"]
