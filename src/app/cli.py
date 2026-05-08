"""Operator CLI. Run via `tsr <command>` after `pip install -e .`."""

from __future__ import annotations

import typer

from app.db import create_all
from app.ingest.run import ingest_all
from app.logging import configure_logging, get_logger

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """trading-signal-research CLI."""
    configure_logging()


@app.command()
def initdb() -> None:
    """Create all tables in the configured database (dev only — prod uses Alembic)."""
    create_all()
    log.info("cli.initdb.done")


@app.command()
def ingest(
    limit: int = typer.Option(25, help="Max recent videos per creator to consider."),
) -> None:
    """Pull recent videos + transcripts for all active creators."""
    results = ingest_all(limit=limit)
    log.info("cli.ingest.done", results=results)


# Placeholders for future phases. Each prints a one-liner so the CLI shape
# is real from the start, even before the implementations land.

@app.command()
def extract(
    limit: int = typer.Option(0, help="Limit number of Documents processed (0 = no limit)."),
    min_segments: int = typer.Option(
        10,
        help=(
            "Skip documents with fewer than this many transcript segments. "
            "Defaults to 10 — short daily videos (Adam Mancini, IBD per-stock clips) "
            "still carry useful tickers and the prefilter handles no-signal segments."
        ),
    ),
    one_pass: bool = typer.Option(
        False, "--one-pass", help="Skip the second validation pass. Faster, less safe."
    ),
    creator: str | None = typer.Option(
        None,
        "--creator",
        help=(
            "Filter to documents from a single creator (case-insensitive substring "
            "match on display_name). Useful for diversifying the corpus instead of "
            "burning budget on the most-recent creator's backlog."
        ),
    ),
) -> None:
    """Run call extractor over new documents. Requires ANTHROPIC_API_KEY."""
    from app.extract.run import run_extraction

    summary = run_extraction(
        limit=limit if limit > 0 else None,
        min_segments=min_segments,
        use_two_pass=not one_pass,
        creator_filter=creator,
    )
    log.info("cli.extract.done", **summary)


@app.command()
def backtest(
    limit: int = typer.Option(0, help="Limit number of calls processed (0 = no limit)."),
) -> None:
    """Compute OutcomeWindows for accepted calls that haven't been evaluated yet."""
    from app.backtest.run import run_backtest

    summary = run_backtest(limit=limit if limit > 0 else None)
    log.info("cli.backtest.done", **summary)


@app.command()
def score() -> None:
    """Recompute creator scorecards from OutcomeWindow data."""
    from app.scoring.creator import run_scoring

    summary = run_scoring()
    log.info("cli.score.done", **summary)


@app.command(name="score-lens-outcomes")
def score_lens_outcomes_cmd(
    max_age_days: int = typer.Option(
        60, help="Snapshots older than this are skipped."
    ),
) -> None:
    """Compute realised outcomes for accumulated lens snapshots.

    For each LensSnapshot whose `as_of + horizon` has elapsed, materialise
    a LensOutcome row at 1d/3d/5d/21d horizons. Idempotent — already-scored
    (snapshot, horizon) pairs are skipped. Run nightly via cron.
    """
    from app.db import session_scope
    from app.scoring.lens_market_adapter import CachedMarketAdapter
    from app.scoring.lens_outcomes import compute_lens_outcomes

    adapter = CachedMarketAdapter()
    with session_scope() as session:
        n = compute_lens_outcomes(
            session, market=adapter, max_age_days=max_age_days
        )
    typer.echo(f"Computed {n} new lens outcomes.")


@app.command(name="aggregate-signals")
def aggregate_signals() -> None:
    """Recompute TickerSignal rows from accepted ExtractedCalls + Claims.

    Per ADR 0006: aggregates per (ticker × window × signal_type), aligned to
    the most recent NYSE close. Idempotent — re-runs replace existing rows
    on the natural key.
    """
    from app.aggregation.ticker_signals import run_aggregation

    summary = run_aggregation()
    log.info("cli.aggregate_signals.done", **summary)


@app.command()
def rank() -> None:
    """Rank new calls with the trained model. (Phase 5)"""
    typer.echo("rank: not implemented yet (Phase 5)")


# Strategy registry — names listed here are valid `tsr backtest-strategy <name>`.
# Adding a new strategy: add an import + dict entry; that's it.
def _strategy_registry() -> dict[str, callable]:
    from app.strategies.bullish_catalyst import BullishCatalystAggregator
    from app.strategies.creator_consensus import CreatorConsensus
    from app.strategies.mention_momentum import MentionMomentum

    return {
        MentionMomentum.name: MentionMomentum,
        BullishCatalystAggregator.name: BullishCatalystAggregator,
        CreatorConsensus.name: CreatorConsensus,
    }


@app.command(name="backtest-strategy")
def backtest_strategy(
    strategy: str = typer.Argument(
        ...,
        help=(
            "Strategy name. Valid: mention_momentum | "
            "bullish_catalyst_aggregator | creator_consensus."
        ),
    ),
    start: str = typer.Option(
        ..., "--start", help="ISO date for the backtest window start (UTC)."
    ),
    end: str = typer.Option(
        ..., "--end", help="ISO date for the backtest window end (UTC)."
    ),
    rebalance: str = typer.Option("weekly", help="Rebalance mode: daily | weekly."),
    cost_bps: int = typer.Option(10, help="Round-trip cost in basis points."),
    min_n_trades: int = typer.Option(
        30, help="Below this, the result is labeled 'underpowered'."
    ),
    persist: bool = typer.Option(
        False, "--persist", help="Persist the result to walkforward_results table."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Print the full result as JSON instead of a table."
    ),
) -> None:
    """Run the walk-forward harness for one strategy + window.

    Per ADR 0007: time-discipline is enforced; strategy never sees data
    after as_of. The harness clamps `end` to `walkforward.t2_cutoff` from
    settings unless that's explicitly bumped.
    """
    from datetime import datetime as _dt, timezone

    from app.backtest.walkforward import (
        WalkForwardConfig,
        persist_result,
        run_walkforward,
    )

    registry = _strategy_registry()
    if strategy not in registry:
        typer.echo(f"unknown strategy: {strategy!r}")
        typer.echo(f"available: {', '.join(sorted(registry))}")
        raise typer.Exit(2)

    start_dt = _dt.fromisoformat(start)
    end_dt = _dt.fromisoformat(end)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    cfg = WalkForwardConfig(
        start=start_dt,
        end=end_dt,
        rebalance=rebalance,
        cost_bps=cost_bps,
        min_n_trades=min_n_trades,
    )
    strategy_obj = registry[strategy]()

    typer.echo(
        f"running {strategy_obj.name}@{strategy_obj.version} "
        f"over [{start_dt.date()} .. {end_dt.date()}] rebalance={rebalance} cost_bps={cost_bps}"
    )
    result = run_walkforward(strategy_obj, cfg)

    if persist:
        row_id = persist_result(result)
        typer.echo(f"persisted as walkforward_results.id={row_id}")

    if json_out:
        import json as _json
        from app.backtest.walkforward import _serialize_result

        payload = _serialize_result(result)
        # Strip JSON-text columns that already are JSON; reparse so output is proper.
        for k in ("metrics_json", "trades_json", "equity_curve_json", "sanity_checks_json", "warnings_json", "config_json"):
            payload[k] = _json.loads(payload[k])
        for k, v in list(payload.items()):
            if isinstance(v, _dt):
                payload[k] = v.isoformat()
        typer.echo(_json.dumps(payload, indent=2, default=str))
        return

    _print_walkforward_result(result)


def _print_walkforward_result(result) -> None:  # noqa: ANN001
    """Pretty-print a WalkForwardResult to the terminal."""
    m = result.metrics

    def pct(v, digits=2):
        return "—" if v is None else f"{v * 100:+.{digits}f}%"

    def num(v, digits=2):
        return "—" if v is None else f"{v:.{digits}f}"

    def ci(v):
        return "—" if v is None else f"[{v[0] * 100:.0f}-{v[1] * 100:.0f}%]"

    typer.echo("")
    typer.echo(
        f"  STRATEGY  {result.strategy_name}@{result.strategy_version}  "
        f"config={result.config_hash}"
    )
    typer.echo(
        f"  WINDOW    {result.config.start.date()} → {result.config.end.date()}  "
        f"rebalance={result.config.rebalance}  cost_bps={result.config.cost_bps}"
    )
    typer.echo(
        f"  RESULT    n_rebalances={result.n_rebalances}  n_trades={m.n_trades}  "
        f"underpowered={m.underpowered}"
    )
    typer.echo("")
    typer.echo("  ── Metrics ──")
    typer.echo(f"    cagr             {pct(m.cagr)}")
    typer.echo(f"    benchmark_cagr   {pct(m.benchmark_cagr)}")
    typer.echo(f"    excess_cagr      {pct(m.excess_cagr)}")
    typer.echo(f"    sharpe           {num(m.sharpe)}")
    typer.echo(f"    max_drawdown     {pct(m.max_drawdown)}")
    typer.echo(f"    annualized_vol   {pct(m.annualized_vol)}")
    typer.echo(f"    hit_rate         {pct(m.hit_rate, 0)}  CI={ci(m.hit_rate_ci)}")
    typer.echo(f"    avg_holding_days {num(m.avg_holding_days, 1)}")
    typer.echo(f"    win_loss_ratio   {num(m.win_loss_ratio)}")
    typer.echo(f"    turnover         {num(m.turnover, 2)}")
    typer.echo("")
    if m.regime_breakdown:
        typer.echo("  ── Regime breakdown ──")
        for regime, stats in m.regime_breakdown.items():
            typer.echo(
                f"    {regime:8s}  n={stats['n']:3d}  "
                f"hr={pct(stats['hit_rate'], 0)}  "
                f"mean_ret={pct(stats.get('mean_return_pct'))}"
            )
        typer.echo("")
    typer.echo("  ── Sanity checks ──")
    for c in result.sanity_checks:
        marker = "✓" if c.passed else "✗"
        typer.echo(f"    {marker} {c.name:30s}  {c.detail}")
    typer.echo("")
    if result.warnings:
        typer.echo(f"  ── Warnings ({len(result.warnings)}) ──")
        for w in result.warnings[:5]:
            typer.echo(f"    · {w}")
        if len(result.warnings) > 5:
            typer.echo(f"    ({len(result.warnings) - 5} more — pass --json to see all)")
        typer.echo("")
    if result.trades:
        typer.echo(f"  ── First 5 trades ──")
        for t in result.trades[:5]:
            typer.echo(
                f"    {t.entry_at.date()} → {t.exit_at.date()}  "
                f"{t.direction} {t.ticker:5s}  ret={t.return_pct * 100:+.2f}%  "
                f"regime={t.regime_at_entry}  weight={t.weight:.2f}"
            )
        typer.echo("")


@app.command()
def research(
    ticker: str = typer.Argument(..., help="Ticker symbol; need not be in configs/universe.csv."),
    history_days: int = typer.Option(540, help="Bars of history to load."),
    benchmark: str = typer.Option("SPY", help="Benchmark for relative-strength."),
    no_metadata: bool = typer.Option(
        False,
        "--no-metadata",
        help="Skip yfinance .info call (faster; identity panel will lack name/sector).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Print the full TickerResearchView as JSON."
    ),
) -> None:
    """Print a research view for a ticker.

    Works for any ticker yfinance can resolve. Combines technical analysis,
    swing-based S/R, setup classification, decision-support status, and
    entry-zone candidate with — if a DB is present — transcript-derived
    signals. Output is decision support, not advice.
    """
    from app.analysis.research import build_ticker_research_view
    from app.db import session_scope

    try:
        with session_scope() as session:
            view = build_ticker_research_view(
                ticker,
                session=session,
                history_days=history_days,
                benchmark_ticker=benchmark,
                fetch_metadata=not no_metadata,
            )
            # Best-effort time-series row.
            from app.analysis.snapshot import persist_snapshot
            persist_snapshot(view, session=session)
    except Exception:
        # Fall back to no-DB path if the database hasn't been initialized.
        view = build_ticker_research_view(
            ticker,
            session=None,
            history_days=history_days,
            benchmark_ticker=benchmark,
            fetch_metadata=not no_metadata,
        )

    if json_out:
        typer.echo(view.model_dump_json(indent=2))
        return

    _print_research_view(view)


@app.command()
def scan(
    tickers: str = typer.Option(
        "",
        "--tickers",
        help="Comma-separated tickers. Mutually exclusive with --watchlist / --universe.",
    ),
    watchlist: bool = typer.Option(
        False,
        "--watchlist",
        help="Use the current watchlist (entity_type='ticker') as the source list.",
    ),
    universe: bool = typer.Option(
        False,
        "--universe",
        help="Use the entire configs/universe.csv as the source list (slow).",
    ),
    no_metadata: bool = typer.Option(False, "--no-metadata"),
    no_persist: bool = typer.Option(
        False, "--no-persist", help="Skip writing ResearchSnapshot rows."
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run research views for a batch of tickers; print a ranked table.

    Daily-research workflow killer:

        tsr scan --watchlist           # status board for everything pinned
        tsr scan --tickers AAPL,NVDA   # ad-hoc multi-ticker
        tsr scan --universe            # full sweep (slow on cold cache)

    Sorted by status quality (research_candidate first, skip_for_now last).
    Writes a ResearchSnapshot per ticker so you build a time-series of
    setup evolution.
    """
    from app.analysis.scan import scan_tickers
    from app.db import session_scope

    # Resolve the ticker list.
    if watchlist:
        from app.models import Watchlist
        from sqlalchemy import select

        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(Watchlist).where(Watchlist.entity_type == "ticker")
                ).all()
            )
            ticker_list = [w.entity_id for w in rows]
        if not ticker_list:
            typer.echo("watchlist is empty — nothing to scan")
            raise typer.Exit(0)
    elif universe:
        from app.normalize.tickers import load_universe

        ticker_list = sorted(load_universe().tickers)
    else:
        if not tickers:
            typer.echo("must pass --tickers, --watchlist, or --universe")
            raise typer.Exit(2)
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]

    typer.echo(f"scanning {len(ticker_list)} ticker(s)…")

    with session_scope() as session:
        result = scan_tickers(
            ticker_list,
            session=session,
            persist=not no_persist,
            fetch_metadata=not no_metadata,
        )

    if json_out:
        typer.echo(result.model_dump_json(indent=2))
        return

    _print_scan_table(result)


def _print_scan_table(result) -> None:  # noqa: ANN001
    """Compact table output for `tsr scan`."""

    def pct(v, d=1):
        return "—" if v is None else f"{v * 100:+.{d}f}%"

    def num(v, d=1):
        return "—" if v is None else f"{v:.{d}f}"

    typer.echo("")
    typer.echo(
        f"  scanned {result.tickers_requested} ticker(s)  ·  "
        f"as_of {result.as_of.isoformat()}"
    )
    typer.echo(
        f"  research_candidates={result.n_research_candidates}  "
        f"watch={result.n_watch}  "
        f"skip={result.n_skip}  "
        f"errors={result.n_errors}"
    )
    typer.echo("")
    header = (
        f"  {'ticker':<6}  {'status':<20}  {'setup':<22}  {'style':<22}  "
        f"{'5d':>7}  {'rsi':>5}  {'atr%':>6}  {'rs63':>7}  {'pullbk':>7}  "
        f"{'brkdst':>7}  {'rr':>5}  {'tx':<3}"
    )
    typer.echo(header)
    typer.echo(f"  {'-' * (len(header) - 2)}")
    for r in result.rows:
        tx = (
            "✓" if r.transcript_confirms == "confirms"
            else "✗" if r.transcript_confirms == "contradicts"
            else "·" if r.transcript_n_claims + r.transcript_n_calls > 0
            else " "
        )
        typer.echo(
            f"  {r.ticker:<6}  "
            f"{(r.status + ' (' + r.status_confidence[0].upper() + ')'):<20}  "
            f"{r.setup_type:<22}  "
            f"{(r.primary_style or '—'):<22}  "
            f"{pct(r.return_5d):>7}  "
            f"{num(r.rsi_14, 0):>5}  "
            f"{pct(r.atr_14_pct, 1):>6}  "
            f"{pct(r.relative_strength_vs_spy_63d):>7}  "
            f"{pct(r.pullback_pct_from_recent_high):>7}  "
            f"{pct(r.breakout_distance_pct):>7}  "
            f"{num(r.risk_reward_estimate, 1):>5}  "
            f"{tx:<3}"
        )
    typer.echo("")
    typer.echo("  legend: status = research_candidate / watch / wait_for_setup / skip_for_now / extended_risk / insufficient_data")
    typer.echo("          tx = transcript: ✓ confirms, ✗ contradicts, · present-but-irrelevant, blank = no transcript data")
    typer.echo("")


def _print_research_view(view) -> None:  # noqa: ANN001 — local helper
    """Pretty-print a TickerResearchView to the terminal.

    Decision-support framing only. Status and setup are explicit; numbers
    carry units; missing fields render as `—` not `None`.
    """
    from app.analysis.schema import TickerResearchView

    assert isinstance(view, TickerResearchView)

    def pct(x: float | None, digits: int = 2) -> str:
        return "—" if x is None else f"{x * 100:+.{digits}f}%"

    def num(x: float | None, digits: int = 2) -> str:
        return "—" if x is None else f"{x:.{digits}f}"

    typer.echo("")
    typer.echo(f"  {view.ticker}  ·  {view.identity.name or '—'}  ·  as_of {view.as_of.isoformat()}")
    typer.echo(f"  exchange={view.identity.exchange or '—'}  sector={view.identity.sector or '—'}  "
               f"in_universe={view.identity.in_universe}")
    typer.echo(f"  bars_loaded={view.identity.n_bars_loaded}  "
               f"freshness_days={view.identity.data_freshness_days}")
    if view.identity.missing_data_warnings:
        for w in view.identity.missing_data_warnings:
            typer.echo(f"  !  {w}")

    typer.echo("")
    typer.echo("  ── Status ──")
    typer.echo(f"  status     = {view.status.status}  (confidence: {view.status.confidence})")
    typer.echo(f"  setup      = {view.setup.setup_type}  (confidence: {view.setup.confidence})")
    typer.echo(f"  primary style = {view.style_fit.primary_style or '—'}")
    typer.echo(f"  summary    = {view.status.summary}")

    typer.echo("")
    typer.echo("  ── Market ──")
    m = view.market
    typer.echo(f"  last_close       {num(m.last_close)}")
    typer.echo(f"  return_5d/21d/63d/252d   {pct(m.return_5d)}  {pct(m.return_21d)}  "
               f"{pct(m.return_63d)}  {pct(m.return_252d)}")
    typer.echo(f"  excess_5d/21d/63d        {pct(m.excess_return_5d)}  {pct(m.excess_return_21d)}  "
               f"{pct(m.excess_return_63d)}")
    typer.echo(f"  52w hi/lo                {num(m.high_52w)} / {num(m.low_52w)}  "
               f"(off_high={pct(m.pct_off_52w_high)})")

    typer.echo("")
    typer.echo("  ── Indicators ──")
    i = view.indicators
    typer.echo(f"  ma stack         {i.ma_alignment}")
    typer.echo(f"  sma 20/50/150/200  {num(i.sma_20)} / {num(i.sma_50)} / "
               f"{num(i.sma_150)} / {num(i.sma_200)}")
    typer.echo(f"  dist sma 50/200  {pct(i.dist_to_sma_50_pct)} / {pct(i.dist_to_sma_200_pct)}")
    typer.echo(f"  slopes 50_21d / 200_63d  {pct(i.sma_50_slope_21d_pct)} / "
               f"{pct(i.sma_200_slope_63d_pct)}")
    typer.echo(f"  rsi14 / atr14% / rv21    {num(i.rsi_14)} / {pct(i.atr_14_pct)} / "
               f"{pct(i.realized_vol_21d_annualized, 0)}")
    typer.echo(f"  vol ratio / RS 63d / 126d  {num(i.volume_ratio_20)} / "
               f"{pct(i.relative_strength_vs_spy_63d)} / {pct(i.relative_strength_vs_spy_126d)}")

    typer.echo("")
    typer.echo("  ── Levels ──")
    lv = view.levels
    typer.echo(f"  nearest support / resistance  {num(lv.nearest_support)} / {num(lv.nearest_resistance)}")
    typer.echo(f"  recent_high_63d / recent_low_63d  {num(lv.recent_high_63d)} / {num(lv.recent_low_63d)}")
    typer.echo(f"  pullback_pct_from_high   {pct(lv.pullback_pct_from_recent_high)}")
    typer.echo(f"  consolidation_range_pct  {pct(lv.consolidation_range_pct)}  "
               f"(tight={lv.is_in_tight_range})")
    typer.echo(f"  breakout_distance_pct    {pct(lv.breakout_distance_pct)}")

    typer.echo("")
    typer.echo("  ── Entry zone ──")
    e = view.entry_zone
    if not e.available:
        typer.echo(f"  not available — {e.reason_unavailable}")
    else:
        typer.echo(f"  trigger          {num(e.setup_trigger_level)}")
        typer.echo(f"  research zone    {num(e.candidate_research_zone_low)}–{num(e.candidate_research_zone_high)}")
        typer.echo(f"  invalidation     {num(e.invalidation_reference)}  "
                   f"(risk: {pct(e.risk_reference_pct)} / {num(e.risk_reference_atrs)} ATR)")
        typer.echo(f"  resistance       {num(e.nearest_resistance)}  "
                   f"(R/R estimate: {num(e.risk_reward_estimate)})")
        for note in e.method_notes:
            typer.echo(f"  · {note}")

    typer.echo("")
    typer.echo("  ── Transcript ──")
    t = view.transcript
    typer.echo(f"  {t.summary}")
    if t.confirms_or_contradicts != "unknown":
        typer.echo(f"  vs setup → {t.confirms_or_contradicts}")
    if t.sample_size_caveat:
        typer.echo(f"  caveat: {t.sample_size_caveat}")

    typer.echo("")
    typer.echo("  ── Why ──")
    for r in view.setup.reasons:
        typer.echo(f"  + {r}")
    for c in view.setup.counterarguments:
        typer.echo(f"  - {c}")
    typer.echo("")
    typer.echo("  ── Rubric ──")
    for entry in view.status.rubric:
        marker = "✓" if entry.passed else ("·" if entry.passed is None else "✗")
        v = entry.value if entry.value is not None else "—"
        th = entry.threshold if entry.threshold is not None else "—"
        typer.echo(f"  {marker} {entry.name:35s}  value={v}  threshold={th}  weight={entry.weight}")
    if view.status.caveats:
        typer.echo("")
        for c in view.status.caveats:
            typer.echo(f"  caveat: {c}")
    typer.echo("")
    typer.echo(f"  {view.disclaimer}")
    typer.echo("")


if __name__ == "__main__":
    app()
