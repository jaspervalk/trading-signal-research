"""Strategy ABC + concrete strategy implementations (ADR 0007).

A `Strategy` is stateless and time-aware: at each rebalance datetime, it sees
only data with `t <= ctx.as_of` and emits zero-or-more `StrategyDecision`s.
The walk-forward harness in `app.backtest.walkforward` is responsible for
constructing leakage-controlled `StrategyContext`s and simulating execution.

Concrete baselines (per ADR 0007):
- MentionMomentum (this session)
- BullishCatalystAggregator (deferred)
- CreatorConsensus (deferred)
"""
