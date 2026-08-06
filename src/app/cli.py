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
    batch: bool = typer.Option(
        False,
        "--batch",
        help=(
            "Submit via the Batches API: same model, prompts and validator at "
            "half the price, but results arrive asynchronously (usually "
            "under an hour, up to 24). Use for backlogs; leave off for the "
            "daily cron, where waiting on a batch to land beats the saving."
        ),
    ),
) -> None:
    """Run call extractor over new documents. Requires ANTHROPIC_API_KEY."""
    from app.extract.run import run_extraction, run_extraction_batched

    runner = run_extraction_batched if batch else run_extraction
    summary = runner(
        limit=limit if limit > 0 else None,
        min_segments=min_segments,
        use_two_pass=not one_pass,
        creator_filter=creator,
    )
    log.info("cli.extract.done", batch=batch, **summary)


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


@app.command(name="lens-scorecards")
def lens_scorecards_cmd(
    lookback_days: int = typer.Option(90, help="Lookback window in days."),
    horizon: str = typer.Option("5d", help="One of 1d, 3d, 5d, 21d."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of table."),
) -> None:
    """Print per-lens accuracy scorecards (Wilson CI + regime/conviction splits)."""
    import json as _json

    from app.db import session_scope
    from app.scoring.lens_scorecards import compute_lens_scorecards

    with session_scope() as session:
        cards = compute_lens_scorecards(
            session, lookback_days=lookback_days, horizon=horizon
        )

    if json_out:
        typer.echo(_json.dumps([card.__dict__ for card in cards], default=str, indent=2))
        return

    if not cards:
        typer.echo("(no lens scorecards — accumulate Deep runs first)")
        return

    for c in cards:
        typer.echo(
            f"{c.lens_name} ({c.horizon}, n={c.n} snapshots "
            f"over {c.n_ticker_days} ticker-days)"
        )
        typer.echo(
            f"  hit_rate: {c.hit_rate:.2%}  [{c.hit_rate_lo:.2%}, {c.hit_rate_hi:.2%}]"
        )
        if c.avg_directional_excess is not None:
            typer.echo(
                f"  avg_directional_excess: {c.avg_directional_excess:+.2%}  "
                f"(n={c.n_directional} directional calls)"
            )
        else:
            typer.echo("  avg_directional_excess: n/a (no directional calls)")
        typer.echo(
            f"  sample avg_excess_vs_spy: {c.avg_excess_vs_spy:+.2%}  "
            "(the tickers, not the lens)"
        )
        if c.by_regime:
            typer.echo(f"  by_regime: {c.by_regime}")
    if cards and min(c.n_ticker_days for c in cards) < 30:
        typer.echo(
            "\nNOTE: snapshots cluster on few ticker-days, so the Wilson CIs "
            "above are narrower than the evidence warrants. Treat "
            "n_ticker_days as the real denominator."
        )


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
    rerank: bool = typer.Option(
        False,
        "--rerank",
        help=(
            "Run reduced 2-lens panel + Sonnet judge per ticker "
            "(~$0.01-0.015 each). Adds RANK + RATIONALE columns."
        ),
    ),
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

    rerank_by_ticker: dict[str, object] = {}
    if rerank:
        from app.research.scan_rerank import rerank_tickers

        typer.echo(f"reranking {len(ticker_list)} ticker(s)…")
        rerank_results = rerank_tickers(ticker_list)
        rerank_by_ticker = {r.ticker: r for r in rerank_results}

    if json_out:
        payload = result.model_dump(mode="json")
        if rerank_by_ticker:
            for row in payload.get("rows", []):
                rr = rerank_by_ticker.get(row["ticker"])
                if rr is not None:
                    row["rank"] = rr.rank
                    row["rationale"] = rr.rationale
                    row["rerank_cost_usd"] = rr.cost_usd
        import json as _json

        typer.echo(_json.dumps(payload, indent=2, default=str))
        return

    _print_scan_table(result, rerank_by_ticker=rerank_by_ticker)


def _print_scan_table(result, rerank_by_ticker: dict | None = None) -> None:  # noqa: ANN001
    """Compact table output for `tsr scan`. Optional rerank columns when
    `rerank_by_ticker` is supplied (maps ticker -> ScanRerankResult)."""

    def pct(v, d=1):
        return "—" if v is None else f"{v * 100:+.{d}f}%"

    def num(v, d=1):
        return "—" if v is None else f"{v:.{d}f}"

    show_rerank = bool(rerank_by_ticker)

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
    if show_rerank:
        header += f"  {'rank':<7}  {'rationale':<60}"
    typer.echo(header)
    typer.echo(f"  {'-' * (len(header) - 2)}")
    for r in result.rows:
        tx = (
            "✓" if r.transcript_confirms == "confirms"
            else "✗" if r.transcript_confirms == "contradicts"
            else "·" if r.transcript_n_claims + r.transcript_n_calls > 0
            else " "
        )
        line = (
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
        if show_rerank:
            rr = rerank_by_ticker.get(r.ticker) if rerank_by_ticker else None
            if rr is None:
                line += f"  {'—':<7}  {'—':<60}"
            else:
                rationale = (rr.rationale or "")[:60]
                line += f"  {rr.rank:<7}  {rationale:<60}"
        typer.echo(line)
    typer.echo("")
    typer.echo("  legend: status = research_candidate / watch / wait_for_setup / skip_for_now / extended_risk / insufficient_data")
    typer.echo("          tx = transcript: ✓ confirms, ✗ contradicts, · present-but-irrelevant, blank = no transcript data")
    if show_rerank:
        typer.echo("          rank = rerank judge priority: high / medium / low / skip")
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


# ---------------------------------------------------------------------------
# Screener (broad-universe funnel — finds tickers to research next)


@app.command(name="screen")
def screen(
    value: bool = typer.Option(False, "--value", help="Enable valuation filter."),
    growth: bool = typer.Option(False, "--growth", help="Enable growth filter."),
    quality: bool = typer.Option(False, "--quality", help="Enable quality filter."),
    technical: bool = typer.Option(
        False, "--technical", help="Enable technical-momentum filter."
    ),
    max_pe: float = typer.Option(
        None, "--max-pe", help="Override the forward P/E ceiling for the value filter."
    ),
    max_peg: float = typer.Option(None, "--max-peg", help="Override max PEG."),
    min_rev_growth: float = typer.Option(
        None, "--min-rev-growth", help="Override min revenue growth (fraction)."
    ),
    sector: str | None = typer.Option(
        None, "--sector", help="Restrict to one sector (case-insensitive)."
    ),
    output: str = typer.Option(
        "table", "--output", help="Format: table | csv | json."
    ),
    universe_path: str | None = typer.Option(
        None, "--universe", help="Override path to screen_universe.csv."
    ),
    limit: int = typer.Option(
        50, "--limit", help="Truncate table to N rows (only affects display)."
    ),
    max_workers: int = typer.Option(
        8, "--workers", help="Concurrent fetch workers."
    ),
    rps: float = typer.Option(
        2.0, "--rps", help="Yfinance requests-per-second cap."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass the 24h disk cache."
    ),
    no_save: bool = typer.Option(
        False, "--no-save", help="Skip writing data/screens/YYYY-MM-DD.json."
    ),
    show_all: bool = typer.Option(
        False, "--all", help="Show every ticker, not just rows that passed a filter."
    ),
) -> None:
    """Run the stock screener funnel over the configured universe.

    Defaults to running ALL filters (a ticker passes the screen if it
    passes ANY enabled filter). Pass any of --value / --growth / --quality
    / --technical to restrict the active set.

    Creator coverage is shown as a bonus column; tickers without coverage
    are NEVER penalised.
    """
    from pathlib import Path

    from app.db import session_scope
    from app.screener.output import render_table, save_json, to_csv, to_json
    from app.screener.pipeline import run_screen
    from app.screener.schema import ScreenConfig
    from app.screener.universe import load_universe

    enabled: list[str] | None = None
    requested = [
        ("value", value),
        ("growth", growth),
        ("quality", quality),
        ("technical", technical),
    ]
    if any(flag for _, flag in requested):
        enabled = [name for name, flag in requested if flag]

    overrides: dict[str, object] = {}
    if max_pe is not None:
        overrides["max_forward_pe"] = max_pe
    if max_peg is not None:
        overrides["max_peg"] = max_peg
    if min_rev_growth is not None:
        overrides["min_revenue_growth"] = min_rev_growth
    if sector is not None:
        overrides["sector"] = sector
    if enabled is not None:
        overrides["enabled_filters"] = enabled

    config = ScreenConfig(**overrides)

    universe_path_p = Path(universe_path) if universe_path else None
    universe = (
        load_universe(universe_path_p, sector=sector)
        if universe_path_p
        else load_universe(sector=sector)
    )
    if not universe:
        typer.echo("Universe is empty after sector filter — nothing to screen.")
        raise typer.Exit(code=1)

    with session_scope() as session:
        result = run_screen(
            universe,
            config=config,
            session=session,
            max_workers=max_workers,
            requests_per_second=rps,
            use_cache=not no_cache,
        )

    if not no_save:
        path = save_json(result)
        log.info("cli.screen.saved", path=str(path))

    fmt = output.lower()
    if fmt == "json":
        typer.echo(to_json(result))
    elif fmt == "csv":
        typer.echo(to_csv(result, only_passing=not show_all))
    else:
        typer.echo(render_table(result, only_passing=not show_all, limit=limit))


@app.command(name="screen-refresh-universe")
def screen_refresh_universe(
    output_path: str | None = typer.Option(
        None, "--output", help="Override path. Defaults to configs/screen_universe.csv."
    ),
) -> None:
    """Fetch S&P 500 + Nasdaq-100 constituents from Wikipedia and rewrite the CSV."""
    from pathlib import Path

    from app.screener.universe import (
        DEFAULT_UNIVERSE_PATH,
        fetch_from_wikipedia,
        write_universe,
    )

    entries = fetch_from_wikipedia()
    path = Path(output_path) if output_path else DEFAULT_UNIVERSE_PATH
    write_universe(entries, path)
    typer.echo(f"Wrote {len(entries)} tickers to {path}")


def _fetch_supply_market_info(ticker: str) -> tuple[float | None, int | None]:
    """(market_cap, analyst_count) for one ticker from yfinance `.info`.

    `.info` is a plain dict with **camelCase** keys (`marketCap`,
    `numberOfAnalystOpinions`) — unlike `fast_info`, which exposes
    snake_case *attributes* but camelCase *dict keys* if you index it. That
    mismatch is the exact bug this function is written to avoid: index
    `.info` with the camelCase key, never `fast_info` with a guessed
    snake_case one. Mirrors the field-plucking approach in
    `app.screener.fetcher._fetch_raw` / `build_metrics`, just narrowed to
    the two fields `app.supply.metrics` needs.

    Never raises — a single ticker's yfinance failure must not sink the
    whole screen, same discipline as `app.supply.screen`.
    """
    import math

    import yfinance as yf

    def _f(x: object) -> float | None:
        if x is None:
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) or math.isinf(v) else v

    try:
        raw_info = yf.Ticker(ticker).info
        info = dict(raw_info) if raw_info else {}
    except Exception as e:  # noqa: BLE001 - one ticker's fetch failure must not sink the batch
        log.warning("cli.supply_screen.yfinance_fetch_failed", ticker=ticker, error=str(e))
        return None, None

    market_cap = _f(info.get("marketCap"))
    analyst_count_f = _f(info.get("numberOfAnalystOpinions"))
    analyst_count = int(analyst_count_f) if analyst_count_f is not None else None
    return market_cap, analyst_count


def _fetch_supply_market_data(
    tickers: list[str],
) -> tuple[dict[str, float], dict[str, int | None]]:
    """market_cap / analyst_count lookups for `run_supply_screen`, cached on
    disk for 24h under `data/cache/supply_yf/` (gitignored, mirrors
    `app.screener.cache`'s shape) so re-running the screen while iterating
    doesn't re-hit yfinance for every one of ~130 tickers every time."""
    from app.config import REPO_ROOT
    from app.screener import cache as yf_cache

    base_dir = REPO_ROOT / "data" / "cache" / "supply_yf"
    market_caps: dict[str, float] = {}
    analyst_counts: dict[str, int | None] = {}
    for ticker in tickers:
        payload = yf_cache.get(ticker, base_dir=base_dir)
        if payload is None:
            market_cap, analyst_count = _fetch_supply_market_info(ticker)
            payload = {"market_cap": market_cap, "analyst_count": analyst_count}
            yf_cache.put(ticker, payload, base_dir=base_dir)
        if payload.get("market_cap") is not None:
            market_caps[ticker] = payload["market_cap"]
        analyst_counts[ticker] = payload.get("analyst_count")
    return market_caps, analyst_counts


def _supply_screen_sort_key(result):  # noqa: ANN001, ANN202 - local helper
    """Rank by earnings_torque descending — "how much gross profit a return
    to their own historical peak would add relative to market cap," per the
    plan's stated goal for this screen. Rows without a computable torque
    (no metrics, or insufficient history) sort last, ticker A-Z beneath
    that, so the table still reads as a single deterministic ordering
    instead of dumping unscored rows in input order at the end."""
    torque = result.metrics.earnings_torque if result.metrics is not None else None
    return (torque is None, -(torque or 0.0), result.ticker)


@app.command(name="supply-screen")
def supply_screen(
    limit: int = typer.Option(50, "--limit", help="Show at most N rows."),
    universe_path: str | None = typer.Option(
        None, "--universe", help="Override path to the ticker universe CSV."
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Layer A supply-constraint screen: gross-margin-compression candidates.

    Resolves each ticker to a SEC CIK, pulls EDGAR companyfacts (7-day disk
    cache) and yfinance market cap / analyst coverage (24h disk cache),
    scores the eight Layer A metrics from `app.supply.metrics`, and prints
    the ranked result plus a coverage summary (resolved / had enough
    history / passed) so a reader can tell how much of the universe EDGAR
    actually covers versus how much failed the filters outright. No
    constraint knowledge, no LLM, no price data — see
    docs/superpowers/plans/2026-08-06-plan-supply-constraint-layer-a.md.
    """
    from pathlib import Path

    from app.screener.universe import DEFAULT_UNIVERSE_PATH, load_universe
    from app.supply.screen import run_supply_screen

    universe_path_p = Path(universe_path) if universe_path else DEFAULT_UNIVERSE_PATH
    universe = load_universe(universe_path_p)
    if not universe:
        typer.echo("Universe is empty — nothing to screen.")
        raise typer.Exit(code=1)

    tickers = [e.ticker for e in universe]
    market_caps, analyst_counts = _fetch_supply_market_data(tickers)

    results = run_supply_screen(tickers, market_caps=market_caps, analyst_counts=analyst_counts)

    resolved = [r for r in results if r.cik is not None]
    had_history = [r for r in resolved if r.metrics is not None and r.metrics.sufficient_history]
    passed = [r for r in results if r.passed]

    ordered = sorted(results, key=_supply_screen_sort_key)
    shown = ordered[:limit]

    if output_json:
        import json as json_module

        payload = {
            "coverage": {
                "universe": len(results),
                "resolved": len(resolved),
                "had_sufficient_history": len(had_history),
                "passed": len(passed),
            },
            "results": [
                {
                    "ticker": r.ticker,
                    "cik": r.cik,
                    "passed": r.passed,
                    "reasons": r.reasons,
                    "metrics": r.metrics.model_dump() if r.metrics is not None else None,
                }
                for r in shown
            ],
        }
        typer.echo(json_module.dumps(payload, indent=2, default=str))
        return

    def pct(v: float | None, d: int = 1) -> str:
        return "—" if v is None else f"{v * 100:.{d}f}%"

    def num(v: float | None, d: int = 2) -> str:
        return "—" if v is None else f"{v:.{d}f}"

    typer.echo("")
    header = (
        f"  {'ticker':<6}  {'passed':<6}  {'gm_pct':>7}  {'headroom':>9}  "
        f"{'torque':>7}  {'cap_int':>8}  {'surviv_q':>9}  {'qtrs':>5}"
    )
    typer.echo(header)
    typer.echo(f"  {'-' * (len(header) - 2)}")
    for r in shown:
        m = r.metrics
        line = (
            f"  {r.ticker:<6}  "
            f"{('yes' if r.passed else 'no'):<6}  "
            f"{pct(m.gm_percentile) if m else '—':>7}  "
            f"{num(m.margin_headroom_pp, 1) if m else '—':>9}  "
            f"{num(m.earnings_torque, 2) if m else '—':>7}  "
            f"{num(m.capital_intensity, 2) if m else '—':>8}  "
            f"{num(m.survivability_quarters, 1) if m else '—':>9}  "
            f"{(m.quarters_of_history if m else '—'):>5}"
        )
        typer.echo(line)
        if not r.passed and r.reasons:
            typer.echo(f"        near-miss: {'; '.join(r.reasons)}")
    typer.echo("")
    typer.echo(
        f"  coverage: universe={len(results)}  resolved_to_cik={len(resolved)}  "
        f"had_sufficient_history={len(had_history)}  passed={len(passed)}"
    )
    typer.echo("")


pf = typer.Typer(no_args_is_help=True, help="Portfolio: manual trade ledger.")
app.add_typer(pf, name="pf")


def pf_cell(value: float | None, width: int) -> str:
    """Render a position-table cell: right-aligned number, or "—" for None/NaN.

    Module-level (not nested in `pf_list`) so it is importable and unit
    testable without touching the database — `pf list` itself runs against
    the real `tsr` DB via `session_scope()`, which the test suite does not
    override, so exercising the formatting has to happen at this level.
    """
    if value is None or value != value:  # NaN check: NaN != NaN
        return f"{'—':>{width}}"
    return f"{value:>{width}.2f}"


@pf.command("add")
def pf_add(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. NVDA."),
    side: str = typer.Option(..., "--side", help="buy | sell"),
    qty: float = typer.Option(..., "--qty", help="Number of shares (fractional allowed)."),
    price: float = typer.Option(..., "--price", help="Price per share, native currency."),
    date: str = typer.Option(..., "--date", help="Trade date, YYYY-MM-DD."),
    currency: str = typer.Option("USD", "--currency", help="Native currency of the stock."),
    fees: float = typer.Option(0.0, "--fees", help="Commission/fees in native currency."),
    eur: float | None = typer.Option(None, "--eur", help="All-in EUR total that moved."),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Record a trade."""
    from datetime import datetime, timezone

    from app.db import session_scope
    from app.portfolio import service
    from app.portfolio.ledger import LedgerError
    from app.portfolio.schema import TradeIn

    try:
        traded_at = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        typer.echo(f"rejected: could not read --date {date!r}; expected YYYY-MM-DD")
        raise typer.Exit(code=1) from exc
    payload = TradeIn(
        ticker=ticker, side=side, quantity=qty, price_per_share=price,
        currency=currency, fees=fees, traded_at=traded_at, eur_amount=eur, note=note,
    )
    with session_scope() as session:
        try:
            row = service.create_trade(session, payload)
        except LedgerError as exc:
            typer.echo(f"rejected: {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo(f"recorded #{row.id}: {row.side} {row.quantity} {row.ticker} @ {row.price_per_share}")


@pf.command("list")
def pf_list(
    include_closed: bool = typer.Option(False, "--include-closed"),
    no_quotes: bool = typer.Option(False, "--no-quotes", help="Skip the live price fetch."),
) -> None:
    """Show positions with live P&L."""
    from app.db import session_scope
    from app.portfolio import service

    with session_scope() as session:
        view = service.build_portfolio_view(session, with_quotes=not no_quotes)

    rows = list(view.open_positions) + (list(view.closed_positions) if include_closed else [])
    if not rows:
        typer.echo("(no positions)")
        return

    typer.echo(f"{'TICKER':<8}{'QTY':>10}{'AVG':>12}{'LAST':>12}{'VALUE':>14}{'P&L':>14}")
    for p in rows:
        typer.echo(
            f"{p.ticker:<8}{p.quantity:>10.4g}"
            f"{pf_cell(p.avg_cost, 12)}"
            f"{pf_cell(p.last_price, 12)}"
            f"{pf_cell(p.market_value, 14)}"
            f"{pf_cell(p.unrealized_pnl, 14)}"
        )
    realized = view.total_realized_pnl
    typer.echo(f"\nrealized P&L: {'—' if realized is None else f'{realized:.2f}'}")
    if view.quote_errors:
        typer.echo(f"no quote for: {', '.join(view.quote_errors)}")


@pf.command("trades")
def pf_trades(ticker: str | None = typer.Option(None, "--ticker")) -> None:
    """Show the raw trade ledger."""
    from app.db import session_scope
    from app.portfolio import service

    with session_scope() as session:
        records = service.list_trades(session, ticker)

    if not records:
        typer.echo("(no trades)")
        return
    for r in records:
        eur = f" eur={r.eur_amount:.2f}" if r.eur_amount is not None else ""
        typer.echo(
            f"#{r.id:<5}{r.traded_at.date()}  {r.side:<4} {r.quantity:>10.4g} "
            f"{r.ticker:<8}@ {r.price_per_share:>10.2f} {r.currency}{eur}"
        )


@pf.command("rm")
def pf_rm(trade_id: int = typer.Argument(..., help="Trade id from `tsr pf trades`.")) -> None:
    """Delete a mistaken trade."""
    from app.db import session_scope
    from app.portfolio import service
    from app.portfolio.ledger import LedgerError

    with session_scope() as session:
        try:
            service.delete_trade(session, trade_id)
        except KeyError as exc:
            typer.echo(f"no trade #{trade_id}")
            raise typer.Exit(code=1) from exc
        except LedgerError as exc:
            typer.echo(f"rejected: {exc}")
            raise typer.Exit(code=1) from exc
    typer.echo(f"deleted #{trade_id}")


if __name__ == "__main__":
    app()
