"""Strategy contract per ADR 0007.

A strategy is a *pure function of context*. The harness constructs a
`StrategyContext` honoring the leakage rules (ADR 0003 + 0007 §"Bias
prevention"), calls `strategy.evaluate(ctx)`, and gets back zero-or-more
`StrategyDecision`s. The harness — never the strategy — is responsible for
simulation, fills, costs, and outcome attribution.

Hard rules:
- A strategy MUST NOT reach into the global database. All inputs come through
  `StrategyContext`.
- A strategy MUST NOT use `datetime.now()` or any wall-clock — only
  `ctx.as_of`. Every read is "what was true at as_of?"
- A strategy emits decisions for tickers in `ctx.universe` only. The harness
  filters to delisted/not-yet-listed tickers per the as_of universe snapshot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol


@dataclass(frozen=True)
class EvidenceRef:
    """Pointer back to the evidence behind a decision.

    Used to render "why this score?" in the dashboard. The harness persists
    these alongside the decision so reviewers can trace any score back to its
    underlying claims/calls/signals.

    `kind` is one of: 'ticker_signal' | 'claim' | 'call' | 'creator_scorecard'
    | 'note'. `id` is the row ID for the kind. `note` is a free-form blurb
    when no concrete row exists (e.g., "ranked top 10% by polarity").
    """

    kind: str
    id: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class StrategyDecision:
    """One decision emitted by a strategy at a single rebalance datetime.

    `score` is the strategy-internal ranking number; the harness does not
    interpret it semantically. Calibration (ADR 0007 §5) compares mean score
    in each decile against realized hit rate, so the score should have a
    monotonic relationship with the strategy's directional thesis.

    `weight` is the portfolio fraction in [0, 1]. The harness enforces sum
    ≤ 1 across the decision set; weights below `min_weight` (configurable on
    the harness) are dropped to avoid micro-positions.

    `holding_period_days` is the trading-day count for V1's simple
    "enter-at-next-open, exit-at-close-after-N-days" simulation model. A
    later strategy that anchors exits to a target / stop will need a richer
    decision shape, but V1 is intentionally conservative.
    """

    ticker: str
    score: float
    direction: str  # 'long' | 'short' | 'flat'
    weight: float
    holding_period_days: int
    evidence: list[EvidenceRef] = field(default_factory=list)
    reasoning: str = ""


class MarketDataReaderProto(Protocol):
    """Minimal contract a strategy expects from the market reader.

    The concrete implementation lives in `app.backtest.market_reader`. The
    Protocol exists so strategies can be unit-tested with a stub reader.
    """

    def daily_bars(self, ticker: str, *, lookback_days: int): ...
    def benchmark_bars(self, *, lookback_days: int): ...


@dataclass(frozen=True)
class StrategyContext:
    """The single input to `Strategy.evaluate`.

    Every field is leakage-controlled — the harness builds these from
    queries with `t <= as_of` and from the universe snapshot at `as_of`.

    `ticker_signals` is a dict keyed by ticker, where each value is the list
    of `TickerSignal`-shaped rows visible at `as_of` (the most-recent row per
    `(window_size, signal_type)` pair). The harness either pulls from a
    pre-computed time-series of signals, or — for historical backtests —
    re-runs the aggregator at `as_of`.

    `creator_scorecards` is a dict keyed by creator_id, holding the most
    recent CreatorScorecard with `computed_at <= as_of`.

    `recent_calls` and `recent_claims` are rows whose `posted_at` is in
    `[as_of - lookback_days, as_of]`, where `lookback_days` is set by the
    harness from the strategy's max signal window.
    """

    as_of: datetime
    universe: list[str]
    ticker_signals: dict[str, list]  # dict[str, list[TickerSignal]]
    creator_scorecards: dict[int, object]  # dict[int, CreatorScorecard]
    recent_calls: list  # list[ExtractedCall-shaped rows]
    recent_claims: list  # list[Claim-shaped rows]
    market: MarketDataReaderProto


class Strategy(ABC):
    """Stateless, time-aware decision producer."""

    name: str = "unnamed"
    version: str = "v0"

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]:
        """Return zero-or-more decisions for `ctx.as_of`."""

    @property
    def required_lookback_days(self) -> int:
        """How many days of historical signals/claims this strategy needs.

        The harness uses this to populate `ctx.recent_calls` /
        `ctx.recent_claims`. Default is 30 — overridden by strategies that
        look further back (e.g. CreatorConsensus needs creator scorecards
        which are typically computed off ≥90d windows).
        """
        return 30


__all__ = [
    "EvidenceRef",
    "MarketDataReaderProto",
    "Strategy",
    "StrategyContext",
    "StrategyDecision",
]
