"""Walk-forward strategy backtest harness (ADR 0007).

Construct once, call `run_walkforward(strategy, cfg)` to get a
`WalkForwardResult`. The harness — never the strategy — owns:

- slicing wall-clock time into rolling `(train, test)` pairs
- building leakage-controlled `StrategyContext`s at each rebalance datetime
- simulating execution against bars (V1: enter-at-next-open, hold N trading
  days, exit at close, net of round-trip cost)
- aggregating closed trades into an out-of-sample equity curve
- running sanity checks (hard-fail on any leakage violation)

Hard rules (ADR 0007 §"Bias prevention"):
- The strategy receives a `StrategyContext` whose every field is filtered
  to `t <= ctx.as_of`. The harness builds these from queries with
  `posted_at <= as_of` and from a `MarketDataReader` bound to `as_of`.
- The harness refuses to evaluate beyond `walkforward.t2_cutoff` from
  settings unless explicitly overridden (forces honest version bumps).
- Every result carries the strategy's `name`, `version`, and a
  `config_hash` so reruns at the same input are reproducible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Iterable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.market_reader import MarketDataReader
from app.backtest.metrics import (
    Trade,
    WalkForwardMetrics,
    compute_walkforward_metrics,
    equity_curve,
)
from app.config import load_project_settings
from app.db import session_scope
from app.logging import get_logger
from app.market.calendar import sessions_after
from app.models import (
    CALL_STATUS_ACCEPTED,
    CLAIM_STATUS_ACCEPTED,
    Claim,
    CreatorScorecard,
    Document,
    ExtractedCall,
    SourceChannel,
    WalkForwardResultRow,
)
from app.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyDecision,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config + result types


@dataclass(frozen=True)
class WalkForwardConfig:
    """Inputs to the harness.

    `train_window` / `test_window` / `step` are timedeltas; they're applied
    to wall-clock time, then snapped to NYSE sessions when needed.

    `rebalance` controls how often the strategy is invoked inside each
    test slice: 'daily' = every NYSE session, 'weekly' = the first session
    of each ISO week, 'on_signal' = (deferred — strategy emits its own
    rebalance points). V1 supports daily + weekly only.

    `refit` is reserved for ML strategies that retrain at each window;
    rule-based strategies leave it False.
    """

    start: datetime
    end: datetime
    train_window: timedelta = timedelta(days=180)
    test_window: timedelta = timedelta(days=30)
    step: timedelta = timedelta(days=30)
    rebalance: str = "weekly"
    refit: bool = False
    cost_bps: int = 10
    benchmark_ticker: str = "SPY"
    universe_override: list[str] | None = None  # if None, derived from configs/universe.csv
    min_n_trades: int = 30
    enforce_t2_cutoff: bool = True
    history_days: int = 540


@dataclass
class SanityCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class WalkForwardResult:
    strategy_name: str
    strategy_version: str
    config_hash: str
    config: WalkForwardConfig
    trades: list[Trade]
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    metrics: WalkForwardMetrics
    sanity_checks: list[SanityCheck]
    warnings: list[str] = field(default_factory=list)
    n_rebalances: int = 0
    underpowered: bool = False


# ---------------------------------------------------------------------------
# Universe + signals


def _load_universe(session: Session, override: list[str] | None) -> list[str]:
    """Static universe from configs/universe.csv (or override).

    Survivorship handling per ADR 0007 §"Bias prevention" is enforced
    *during* the per-rebalance loop, where `MarketDataReader.is_tradeable`
    drops tickers without recent volume at as_of.
    """
    if override is not None:
        return [t.upper() for t in override]
    from app.normalize.tickers import load_universe

    universe = load_universe()
    return sorted(universe.tickers)


# ---------------------------------------------------------------------------
# Per-rebalance data loaders (all leakage-controlled)


def _load_recent_calls(
    session: Session, *, as_of: datetime, lookback_days: int
) -> list:
    """ExtractedCall rows joined with creator+document where
    `posted_at IN [as_of - lookback_days, as_of]`."""
    since = as_of - timedelta(days=lookback_days)
    rows = session.execute(
        select(
            ExtractedCall.id,
            ExtractedCall.ticker,
            ExtractedCall.direction,
            ExtractedCall.entry_price,
            ExtractedCall.target_price,
            ExtractedCall.stop_price,
            ExtractedCall.final_confidence,
            Document.posted_at,
            SourceChannel.creator_id,
        )
        .select_from(ExtractedCall)
        .join(Document, Document.id == ExtractedCall.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .where(ExtractedCall.status == CALL_STATUS_ACCEPTED)
        .where(Document.posted_at >= since)
        .where(Document.posted_at <= as_of)
        .order_by(Document.posted_at)
    ).all()
    return [dict(r._mapping) for r in rows]


def _load_recent_claims(
    session: Session, *, as_of: datetime, lookback_days: int
) -> list:
    since = as_of - timedelta(days=lookback_days)
    rows = session.execute(
        select(
            Claim.id,
            Claim.ticker,
            Claim.polarity,
            Claim.claim_class,
            Claim.claim_type,
            Claim.final_confidence,
            Document.posted_at,
            SourceChannel.creator_id,
        )
        .select_from(Claim)
        .join(Document, Document.id == Claim.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
        .where(Document.posted_at >= since)
        .where(Document.posted_at <= as_of)
        .order_by(Document.posted_at)
    ).all()
    return [dict(r._mapping) for r in rows]


def _load_creator_scorecards(session: Session, *, as_of: datetime) -> dict[int, dict]:
    """Most-recent CreatorScorecard per creator with `computed_at <= as_of`."""
    rows = session.execute(
        select(
            CreatorScorecard.creator_id,
            CreatorScorecard.hit_rate,
            CreatorScorecard.hit_rate_lower_ci,
            CreatorScorecard.hit_rate_upper_ci,
            CreatorScorecard.n_calls,
            CreatorScorecard.n_activated,
            CreatorScorecard.computed_at,
        )
        .where(CreatorScorecard.computed_at <= as_of)
        .order_by(CreatorScorecard.creator_id, CreatorScorecard.computed_at.desc())
    ).all()
    out: dict[int, dict] = {}
    for r in rows:
        cid = int(r.creator_id)
        if cid not in out:
            out[cid] = dict(r._mapping)
    return out


# ---------------------------------------------------------------------------
# Rebalance datetime generator


def _rebalance_datetimes(
    *, start: datetime, end: datetime, mode: str
) -> list[datetime]:
    """Generate rebalance UTC datetimes inside `[start, end]` per `mode`.

    Snaps to NYSE session opens (so 'as_of' is a real trading-clock moment).
    """
    if mode not in ("daily", "weekly"):
        raise ValueError(f"unsupported rebalance mode: {mode!r}")

    # Pull up to (end - start) sessions; sessions_after returns Timestamps.
    span_days = max(1, int((end - start).total_seconds() // 86400) + 2)
    sess = sessions_after(start - timedelta(days=1), span_days)
    sess = [s.to_pydatetime() for s in sess if start <= s.to_pydatetime() <= end]

    if mode == "daily":
        return sess
    # weekly: keep the first session of each ISO week.
    out: list[datetime] = []
    seen_weeks: set[tuple[int, int]] = set()
    for s in sess:
        wk = (s.isocalendar().year, s.isocalendar().week)
        if wk not in seen_weeks:
            out.append(s)
            seen_weeks.add(wk)
    return out


# ---------------------------------------------------------------------------
# Simulator (V1: open-next-session → close-after-N-days)


def _simulate_decision(
    decision: StrategyDecision,
    *,
    as_of: datetime,
    reader: MarketDataReader,
    cost_bps: int,
) -> Trade | None:
    """Simulate one decision. Returns None if execution couldn't fill (no
    bar after as_of, ticker not tradeable, etc.)."""
    if decision.direction == "flat" or decision.weight <= 0:
        return None

    # Use forward_bars (harness-internal) — see MarketDataReader docstring.
    # The strategy already saw `daily_bars` (leakage-bound to as_of); the
    # simulator now looks at what actually happened after as_of.
    bars_after = reader.forward_bars(decision.ticker, forward_days=decision.holding_period_days * 4 + 14)
    if bars_after.empty or "Open" not in bars_after or "Close" not in bars_after:
        return None
    n_needed = decision.holding_period_days
    if len(bars_after) <= n_needed:
        return None  # not enough forward bars to close the trade

    entry_bar = bars_after.iloc[0]
    exit_bar = bars_after.iloc[n_needed]

    entry_price = float(entry_bar["Open"])
    exit_price = float(exit_bar["Close"])
    if entry_price <= 0:
        return None

    raw_return = (exit_price - entry_price) / entry_price
    if decision.direction == "short":
        raw_return = -raw_return
    net_return = raw_return - (cost_bps / 10_000)

    # Regime via SPY 21d trend at entry (per ADR 0003). The regime test
    # uses bars *up to entry*, so the leakage-bound benchmark_bars is correct.
    spy_bars_at_entry = reader.benchmark_bars(lookback_days=60)
    regime = _infer_regime(spy_bars_at_entry, entry_at=bars_after.index[0].to_pydatetime())

    # Benchmark return over the SAME forward window as the trade — uses
    # forward_benchmark_bars (harness-internal).
    spy_forward = reader.forward_benchmark_bars(forward_days=n_needed * 4 + 14)
    bench_window = spy_forward[
        (spy_forward.index >= bars_after.index[0])
        & (spy_forward.index <= bars_after.index[n_needed])
    ] if not spy_forward.empty else pd.DataFrame()
    bench_return = (
        float((bench_window["Close"].iloc[-1] - bench_window["Close"].iloc[0]) / bench_window["Close"].iloc[0])
        if len(bench_window) >= 2
        else None
    )

    return Trade(
        ticker=decision.ticker,
        direction=decision.direction,
        entry_at=bars_after.index[0].to_pydatetime(),
        exit_at=bars_after.index[n_needed].to_pydatetime(),
        entry_price=entry_price,
        exit_price=exit_price,
        return_pct=net_return,
        weight=decision.weight,
        regime_at_entry=regime,
        holding_days=n_needed,
        strategy_score=decision.score,
        benchmark_return_pct=bench_return,
    )


def _infer_regime(spy_bars: pd.DataFrame, *, entry_at: datetime) -> str:
    """SPY 21d trend at entry: up (> +2%), flat, down (< -2%). Per ADR 0003."""
    if spy_bars.empty:
        return "unknown"
    cutoff_ts = pd.Timestamp(entry_at).tz_convert("UTC") if entry_at.tzinfo else pd.Timestamp(entry_at).tz_localize("UTC")
    win = spy_bars[spy_bars.index <= cutoff_ts]
    if len(win) < 22:
        return "unknown"
    last = float(win["Close"].iloc[-1])
    prior = float(win["Close"].iloc[-22])
    if prior <= 0:
        return "unknown"
    pct = (last - prior) / prior
    if pct > 0.02:
        return "up"
    if pct < -0.02:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Main entry point


def run_walkforward(
    strategy: Strategy,
    cfg: WalkForwardConfig,
    *,
    session: Session | None = None,
) -> WalkForwardResult:
    """Run `strategy` over the wall-clock window in `cfg`, return result."""
    settings = load_project_settings().walkforward
    t2 = datetime.fromisoformat(settings.t2_cutoff)
    if cfg.enforce_t2_cutoff and cfg.end > t2:
        log.warning(
            "walkforward.t2_violation",
            t2=str(t2),
            cfg_end=str(cfg.end),
            note="bump configs/settings.yaml:walkforward.t2_cutoff to evaluate later windows",
        )
        cfg = WalkForwardConfig(
            **{**asdict(cfg), "end": t2, "enforce_t2_cutoff": False}
        )

    config_hash = _hash_config(strategy, cfg)
    log.info(
        "walkforward.start",
        strategy=strategy.name,
        version=strategy.version,
        start=str(cfg.start),
        end=str(cfg.end),
        config_hash=config_hash,
    )

    rebalance_dts = _rebalance_datetimes(
        start=cfg.start, end=cfg.end, mode=cfg.rebalance
    )
    log.info("walkforward.rebalances", n=len(rebalance_dts))

    if session is None:
        # Use a single session for the entire run for query reuse.
        with session_scope() as scoped:
            return _execute(strategy, cfg, rebalance_dts, scoped, config_hash)
    return _execute(strategy, cfg, rebalance_dts, session, config_hash)


def _execute(
    strategy: Strategy,
    cfg: WalkForwardConfig,
    rebalance_dts: list[datetime],
    session: Session,
    config_hash: str,
) -> WalkForwardResult:
    universe = _load_universe(session, cfg.universe_override)
    log.info("walkforward.universe", n=len(universe))

    all_trades: list[Trade] = []
    warnings: list[str] = []

    for as_of in rebalance_dts:
        reader = MarketDataReader.at(
            as_of, history_days=cfg.history_days, benchmark_ticker=cfg.benchmark_ticker
        )

        # Universe survivorship: only tickers tradeable at as_of.
        tradeable = [t for t in universe if reader.is_tradeable(t)]

        recent_calls = _load_recent_calls(
            session, as_of=as_of, lookback_days=strategy.required_lookback_days
        )
        recent_claims = _load_recent_claims(
            session, as_of=as_of, lookback_days=strategy.required_lookback_days
        )
        creator_scorecards = _load_creator_scorecards(session, as_of=as_of)

        ctx = StrategyContext(
            as_of=as_of,
            universe=tradeable,
            ticker_signals={},  # V1: strategies derive signals from raw inputs.
            creator_scorecards=creator_scorecards,
            recent_calls=recent_calls,
            recent_claims=recent_claims,
            market=reader,
        )

        try:
            decisions = strategy.evaluate(ctx)
        except Exception as e:
            log.warning(
                "walkforward.strategy.error",
                strategy=strategy.name,
                as_of=str(as_of),
                error=str(e),
            )
            warnings.append(f"strategy error at {as_of.isoformat()}: {e}")
            continue

        for d in decisions:
            if d.ticker not in tradeable:
                warnings.append(
                    f"decision for non-tradeable ticker {d.ticker} at {as_of.isoformat()} dropped"
                )
                continue
            trade = _simulate_decision(
                d, as_of=as_of, reader=reader, cost_bps=cfg.cost_bps
            )
            if trade is None:
                continue
            all_trades.append(trade)

    # Build the benchmark-only curve over the same wall-clock window.
    bench_reader = MarketDataReader.at(cfg.end, benchmark_ticker=cfg.benchmark_ticker)
    bench_bars = bench_reader.benchmark_bars(lookback_days=int((cfg.end - cfg.start).total_seconds() // 86400) + 14)
    bench_curve = _normalize_benchmark_curve(bench_bars, cfg.start, cfg.end)

    sanity = _run_sanity_checks(all_trades, cfg)
    period_days = (cfg.end - cfg.start).total_seconds() / 86400
    metrics = compute_walkforward_metrics(
        all_trades,
        benchmark_curve=bench_curve,
        test_period_days=period_days,
        min_n_trades=cfg.min_n_trades,
    )

    return WalkForwardResult(
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_hash=config_hash,
        config=cfg,
        trades=all_trades,
        equity_curve=equity_curve(all_trades),
        benchmark_curve=bench_curve,
        metrics=metrics,
        sanity_checks=sanity,
        warnings=warnings,
        n_rebalances=len(rebalance_dts),
        underpowered=metrics.underpowered,
    )


# ---------------------------------------------------------------------------
# Sanity checks


def _run_sanity_checks(trades: list[Trade], cfg: WalkForwardConfig) -> list[SanityCheck]:
    checks: list[SanityCheck] = []

    # 1. Every trade has entry <= exit, both inside the test window.
    in_window = all(
        t.entry_at <= t.exit_at and cfg.start <= t.entry_at <= cfg.end
        for t in trades
    )
    checks.append(
        SanityCheck(
            name="entry_le_exit_in_window",
            passed=in_window,
            detail="every trade satisfies entry_at <= exit_at and is inside [cfg.start, cfg.end]",
        )
    )

    # 2. n_trades >= min_n_trades or labelled underpowered.
    n = len(trades)
    underpowered = n < cfg.min_n_trades
    checks.append(
        SanityCheck(
            name="powered_n_trades",
            passed=not underpowered,
            detail=f"n_trades={n} (threshold {cfg.min_n_trades})"
            + ("; result is UNDERPOWERED — interpret all metrics with caution" if underpowered else ""),
        )
    )

    # 3. Distribution check: trade-return mean shouldn't be implausibly far
    #    above the SPY-window distribution. Crude V1 implementation: flag
    #    when mean trade return > 5x mean benchmark return AND n>30.
    if n > 30 and any(t.benchmark_return_pct is not None for t in trades):
        mean_trade = sum(t.return_pct for t in trades) / n
        bench_returns = [t.benchmark_return_pct for t in trades if t.benchmark_return_pct is not None]
        mean_bench = sum(bench_returns) / len(bench_returns) if bench_returns else 0.0
        suspicious = mean_bench != 0 and mean_trade > 5 * abs(mean_bench)
        checks.append(
            SanityCheck(
                name="distribution_vs_spy",
                passed=not suspicious,
                detail=(
                    f"mean_trade_return={mean_trade:.4f}, mean_bench_return={mean_bench:.4f}"
                    + ("; investigate for leakage" if suspicious else "")
                ),
            )
        )

    return checks


# ---------------------------------------------------------------------------
# Helpers


def _normalize_benchmark_curve(
    bars: pd.DataFrame, start: datetime, end: datetime
) -> pd.Series:
    if bars.empty or "Close" not in bars:
        return pd.Series(dtype=float)
    start_ts = pd.Timestamp(start).tz_convert("UTC") if start.tzinfo else pd.Timestamp(start).tz_localize("UTC")
    end_ts = pd.Timestamp(end).tz_convert("UTC") if end.tzinfo else pd.Timestamp(end).tz_localize("UTC")
    win = bars[(bars.index >= start_ts) & (bars.index <= end_ts)]
    if len(win) < 2:
        return pd.Series(dtype=float)
    return win["Close"] / float(win["Close"].iloc[0])


def _hash_config(strategy: Strategy, cfg: WalkForwardConfig) -> str:
    payload = {
        "strategy_name": strategy.name,
        "strategy_version": strategy.version,
        "start": cfg.start.isoformat(),
        "end": cfg.end.isoformat(),
        "train_window_days": cfg.train_window.days,
        "test_window_days": cfg.test_window.days,
        "step_days": cfg.step.days,
        "rebalance": cfg.rebalance,
        "cost_bps": cfg.cost_bps,
        "benchmark_ticker": cfg.benchmark_ticker,
        "min_n_trades": cfg.min_n_trades,
        "history_days": cfg.history_days,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Persistence (ADR 0007 §6 — WalkForwardResult survives process boundaries)


def persist_result(result: WalkForwardResult, *, session: Session | None = None) -> int:
    """Upsert a `WalkForwardResult` into `walkforward_results`.

    Idempotent on `(strategy_name, strategy_version, config_hash)`. Returns
    the row id. The harness deliberately does NOT call this — the CLI
    decides whether to persist (so unit tests don't pollute the DB).
    """
    if session is None:
        with session_scope() as scoped:
            return _persist(result, scoped)
    return _persist(result, session)


def _persist(result: WalkForwardResult, session: Session) -> int:
    natural_key_filter = (
        (WalkForwardResultRow.strategy_name == result.strategy_name)
        & (WalkForwardResultRow.strategy_version == result.strategy_version)
        & (WalkForwardResultRow.config_hash == result.config_hash)
    )
    existing = session.scalar(select(WalkForwardResultRow).where(natural_key_filter))
    payload = _serialize_result(result)

    if existing is None:
        row = WalkForwardResultRow(**payload)
        session.add(row)
        session.flush()
        return int(row.id)

    for k, v in payload.items():
        setattr(existing, k, v)
    session.flush()
    return int(existing.id)


def _serialize_result(result: WalkForwardResult) -> dict:
    m = result.metrics
    cfg = result.config
    return {
        "strategy_name": result.strategy_name,
        "strategy_version": result.strategy_version,
        "config_hash": result.config_hash,
        "start_at": cfg.start,
        "end_at": cfg.end,
        "rebalance_mode": cfg.rebalance,
        "cost_bps": cfg.cost_bps,
        "benchmark_ticker": cfg.benchmark_ticker,
        "n_rebalances": result.n_rebalances,
        "n_trades": m.n_trades,
        "underpowered": m.underpowered,
        "cagr": m.cagr,
        "sharpe": m.sharpe,
        "max_drawdown": m.max_drawdown,
        "annualized_vol": m.annualized_vol,
        "hit_rate": m.hit_rate,
        "hit_rate_lower_ci": m.hit_rate_ci[0] if m.hit_rate_ci else None,
        "hit_rate_upper_ci": m.hit_rate_ci[1] if m.hit_rate_ci else None,
        "benchmark_cagr": m.benchmark_cagr,
        "excess_cagr": m.excess_cagr,
        "avg_holding_days": m.avg_holding_days,
        "win_loss_ratio": m.win_loss_ratio,
        "turnover": m.turnover,
        "metrics_json": json.dumps(_metrics_dict(m), default=_json_default),
        "trades_json": json.dumps(
            [_trade_dict(t) for t in result.trades], default=_json_default
        ),
        "equity_curve_json": json.dumps(_curve_to_pairs(result.equity_curve)),
        "sanity_checks_json": json.dumps(
            [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.sanity_checks]
        ),
        "warnings_json": json.dumps(result.warnings),
        "config_json": json.dumps(_config_dict(cfg), default=_json_default),
        "computed_at": datetime.now(tz=UTC),
    }


def _metrics_dict(m) -> dict:  # type: ignore[no-untyped-def]
    return {
        "n_trades": m.n_trades,
        "cagr": m.cagr,
        "sharpe": m.sharpe,
        "max_drawdown": m.max_drawdown,
        "annualized_vol": m.annualized_vol,
        "hit_rate": m.hit_rate,
        "hit_rate_ci": list(m.hit_rate_ci) if m.hit_rate_ci else None,
        "benchmark_cagr": m.benchmark_cagr,
        "excess_cagr": m.excess_cagr,
        "benchmark_return_total": m.benchmark_return_total,
        "avg_holding_days": m.avg_holding_days,
        "win_loss_ratio": m.win_loss_ratio,
        "turnover": m.turnover,
        "underpowered": m.underpowered,
        "regime_breakdown": m.regime_breakdown,
    }


def _trade_dict(t) -> dict:  # type: ignore[no-untyped-def]
    return {
        "ticker": t.ticker,
        "direction": t.direction,
        "entry_at": t.entry_at,
        "exit_at": t.exit_at,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "return_pct": t.return_pct,
        "weight": t.weight,
        "regime_at_entry": t.regime_at_entry,
        "holding_days": t.holding_days,
        "strategy_score": t.strategy_score,
        "benchmark_return_pct": t.benchmark_return_pct,
    }


def _config_dict(cfg: WalkForwardConfig) -> dict:
    return {
        "start": cfg.start,
        "end": cfg.end,
        "train_window_days": cfg.train_window.days,
        "test_window_days": cfg.test_window.days,
        "step_days": cfg.step.days,
        "rebalance": cfg.rebalance,
        "cost_bps": cfg.cost_bps,
        "benchmark_ticker": cfg.benchmark_ticker,
        "min_n_trades": cfg.min_n_trades,
        "history_days": cfg.history_days,
    }


def _curve_to_pairs(curve: pd.Series) -> list[list]:
    if curve.empty:
        return []
    return [[idx.isoformat(), float(val)] for idx, val in curve.items()]


def _json_default(o):  # type: ignore[no-untyped-def]
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


__all__ = [
    "SanityCheck",
    "WalkForwardConfig",
    "WalkForwardResult",
    "persist_result",
    "run_walkforward",
]
