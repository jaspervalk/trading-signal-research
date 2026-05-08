"""MentionMomentum — the simplest of ADR 0007's three baseline strategies.

Thesis: tickers receiving an unusual amount of bullishly-polarized creator
mentions over a short window tend to continue moving in that direction over
the next few sessions. We rank tickers by `net_polarity × n_distinct_creators`
over a 7d lookback, take the top decile (long), equal-weight, hold 5 trading
days.

Per ADR 0007 §"Bias prevention", this strategy:
- never queries the DB directly — it reads only `ctx.recent_claims`
- never uses `datetime.now()` — only `ctx.as_of`
- never references prices > `ctx.as_of` — entries/exits are simulated by
  the harness, not the strategy

Hyperparameters (frozen for V1 — bumping requires a `version` bump):
- lookback_days = 7
- min_creators_per_ticker = 1 (we don't have N to be picky yet; will tighten
  to ≥2 once Stockbee + extraction backlog land)
- top_fraction = 0.10 (top decile)
- holding_period_days = 5 (matches the project's primary horizon)
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


_POLARITY_VALUE = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
    "mixed": 0.0,
}


class MentionMomentum(Strategy):
    """Long the top-decile-by-polarity-mentions tickers; hold 5 trading days."""

    name = "mention_momentum"
    version = "v0.1"

    def __init__(
        self,
        *,
        lookback_days: int = 7,
        min_creators_per_ticker: int = 1,
        top_fraction: float = 0.10,
        holding_period_days: int = 5,
    ):
        if not 0 < top_fraction <= 1:
            raise ValueError("top_fraction must be in (0, 1]")
        if min_creators_per_ticker < 1:
            raise ValueError("min_creators_per_ticker must be >= 1")
        self.lookback_days = lookback_days
        self.min_creators_per_ticker = min_creators_per_ticker
        self.top_fraction = top_fraction
        self.holding_period_days = holding_period_days

    @property
    def required_lookback_days(self) -> int:
        # Pull a small safety margin past lookback_days so the harness
        # captures all needed claims.
        return max(self.lookback_days, 14)

    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]:
        # Filter claims to the lookback window strictly inside ctx.as_of.
        cutoff = ctx.as_of - timedelta(days=self.lookback_days)
        scoped = [
            c for c in ctx.recent_claims
            if c.get("ticker")
            and c.get("posted_at") is not None
            and c["posted_at"] >= cutoff
            and c["posted_at"] <= ctx.as_of
            and c.get("ticker") in set(ctx.universe)
        ]

        if not scoped:
            return []

        # Group by ticker, compute net_polarity × n_distinct_creators.
        by_ticker: dict[str, list[dict]] = defaultdict(list)
        for c in scoped:
            by_ticker[c["ticker"]].append(c)

        scored: list[tuple[str, float, int, int]] = []  # (ticker, score, n_mentions, n_creators)
        for ticker, claims in by_ticker.items():
            creators = {c["creator_id"] for c in claims if c.get("creator_id") is not None}
            n_creators = len(creators)
            if n_creators < self.min_creators_per_ticker:
                continue
            net_polarity = sum(
                _POLARITY_VALUE.get(c["polarity"], 0.0) for c in claims
            ) / max(1, len(claims))
            score = net_polarity * n_creators
            if score <= 0:
                continue  # long-only V1: ignore non-bullish scores
            scored.append((ticker, score, len(claims), n_creators))

        if not scored:
            return []

        # Top decile (or at least 1 ticker), equal-weighted.
        scored.sort(key=lambda x: x[1], reverse=True)
        n_pick = max(1, int(len(scored) * self.top_fraction + 0.999))
        chosen = scored[:n_pick]
        weight = 1.0 / len(chosen)

        decisions: list[StrategyDecision] = []
        for ticker, score, n_mentions, n_creators in chosen:
            evidence = [
                EvidenceRef(kind="claim", id=c["id"])
                for c in by_ticker[ticker]
            ]
            decisions.append(
                StrategyDecision(
                    ticker=ticker,
                    score=score,
                    direction="long",
                    weight=weight,
                    holding_period_days=self.holding_period_days,
                    evidence=evidence,
                    reasoning=(
                        f"polarity={score / max(1, n_creators):+.2f} × "
                        f"n_creators={n_creators}; "
                        f"{n_mentions} claim(s) in last {self.lookback_days}d"
                    ),
                )
            )
        return decisions


__all__ = ["MentionMomentum"]
