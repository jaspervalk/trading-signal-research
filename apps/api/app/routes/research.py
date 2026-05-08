"""Research-scan + snapshot-history endpoints.

Built so the dashboard can render a "watchlist research board" without
making N round-trip calls to /tickers/{t}/research, and so the user can
see a ticker's setup evolution over time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.scan import ScanResult, scan_tickers
from app.models import ResearchSnapshot, Watchlist
from app.research import cache as plan_cache
from app.research import context as plan_context
from app.research import deep as plan_deep
from app.research import quick as plan_quick
from app.research.scan_rerank import rerank_tickers
from app.research.schema import EntryExitPlan
from app.scoring.lens_scorecards import compute_lens_scorecards
from apps.api.app.deps import db_session

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/scan", response_model=ScanResult)
def scan(
    tickers: str = Query(
        "",
        description=(
            "Comma-separated list of tickers. Mutually exclusive with `source`. "
            "Pass either this OR `source=watchlist`."
        ),
    ),
    source: str | None = Query(
        None,
        description=(
            "Source for the ticker list. 'watchlist' uses the user's "
            "current watchlist (entity_type='ticker'). When set, `tickers` is ignored."
        ),
    ),
    persist: bool = Query(
        True,
        description="Write a ResearchSnapshot per ticker (default on for time-series).",
    ),
    fetch_metadata: bool = Query(False),
    history_days: int = Query(540, ge=120, le=2000),
    session: Session = Depends(db_session),
) -> ScanResult:
    """Run research views for a batch of tickers; return ranked.

    Cost: O(N) yfinance fetches on cold cache. Warm cache (typical): ~50ms
    per ticker. The scan is sequential to keep yfinance happy.
    """
    if source == "watchlist":
        rows = list(
            session.scalars(
                select(Watchlist).where(Watchlist.entity_type == "ticker")
            ).all()
        )
        ticker_list = [w.entity_id for w in rows]
    else:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]

    if not ticker_list:
        # Empty input → empty result; let the frontend render an empty state.
        return ScanResult(
            as_of=datetime.now(tz=UTC),
            tickers_requested=0,
            rows=[],
            n_research_candidates=0,
            n_watch=0,
            n_skip=0,
            n_errors=0,
        )

    return scan_tickers(
        ticker_list,
        session=session,
        persist=persist,
        fetch_metadata=fetch_metadata,
        history_days=history_days,
    )


@router.get("/snapshots/{ticker}")
def get_ticker_snapshots(
    ticker: str,
    days: int = Query(30, ge=1, le=365, description="History window in calendar days."),
    limit: int = Query(200, ge=1, le=2000),
    session: Session = Depends(db_session),
) -> list[dict]:
    """Time-series of `ResearchSnapshot` rows for `ticker`, oldest-first.

    Powers the dashboard's "how this ticker's setup evolved" view.
    """
    ticker = ticker.upper()
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    rows = list(
        session.scalars(
            select(ResearchSnapshot)
            .where(ResearchSnapshot.ticker == ticker)
            .where(ResearchSnapshot.as_of >= cutoff)
            .order_by(ResearchSnapshot.as_of)
            .limit(limit)
        ).all()
    )
    return [
        {
            "ticker": r.ticker,
            "as_of": r.as_of,
            "status": r.status,
            "status_confidence": r.status_confidence,
            "setup_type": r.setup_type,
            "setup_confidence": r.setup_confidence,
            "primary_style": r.primary_style,
            "last_close": r.last_close,
            "pct_off_52w_high": r.pct_off_52w_high,
            "rsi_14": r.rsi_14,
            "atr_14_pct": r.atr_14_pct,
            "ma_alignment": r.ma_alignment,
            "sma_50_slope_21d_pct": r.sma_50_slope_21d_pct,
            "relative_strength_vs_spy_63d": r.relative_strength_vs_spy_63d,
            "pullback_pct_from_recent_high": r.pullback_pct_from_recent_high,
            "breakout_distance_pct": r.breakout_distance_pct,
            "entry_zone_available": r.entry_zone_available,
            "entry_trigger": r.entry_trigger,
            "transcript_n_calls": r.transcript_n_calls,
            "transcript_n_claims": r.transcript_n_claims,
            "transcript_polarity_30d": r.transcript_polarity_30d,
            "transcript_confirms": r.transcript_confirms,
            "computed_at": r.computed_at,
        }
        for r in rows
    ]


@router.post("/quick/{ticker}", response_model=EntryExitPlan)
def run_quick_research(
    ticker: str,
    force: bool = Query(
        False,
        description="Bypass the same-day cache and run a fresh LLM call.",
    ),
    session: Session = Depends(db_session),
) -> EntryExitPlan:
    """Run quick-mode entry/exit research for `ticker`.

    Same `(ticker, day)` is cached (return cost_usd=0 for cache hit unless
    `force=true`). On cache miss this runs one Claude Haiku call (~$0.01,
    3-5s) grounded by the existing `TickerResearchView` + recent claims.
    """
    ticker = ticker.upper()
    if not force:
        cached = plan_cache.get_cached(session=session, ticker=ticker, mode="quick")
        if cached is not None:
            return cached

    try:
        packet = plan_context.gather(ticker=ticker, session=session)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to gather research packet: {e}"
        ) from e

    try:
        result = plan_quick.run(packet)
    except RuntimeError as e:
        # Most likely an API-key or upstream Anthropic problem.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Quick research failed: {e}"
        ) from e

    # Don't cache plans missing the lens panel — a re-run will retry and likely
    # succeed (Anthropic doesn't strictly enforce tool-input schemas, so an
    # occasional truncation is expected). The user still gets the partial plan
    # in this response; only the cache is skipped so they're not stuck with it.
    if result.plan.lenses:
        plan_cache.store(session=session, plan=result.plan)
        session.commit()
    return result.plan


@router.get("/quick/{ticker}", response_model=EntryExitPlan | None)
def get_quick_research(
    ticker: str,
    session: Session = Depends(db_session),
) -> EntryExitPlan | None:
    """Return today's cached quick-mode plan for `ticker`, or null."""
    return plan_cache.get_cached(session=session, ticker=ticker.upper(), mode="quick")


@router.post("/deep/{ticker}", response_model=EntryExitPlan)
def run_deep_research(
    ticker: str,
    force: bool = Query(
        False,
        description="Bypass the same-day cache and run a fresh multi-agent debate.",
    ),
    session: Session = Depends(db_session),
) -> EntryExitPlan:
    """Run deep-mode entry/exit research for `ticker`.

    Spawns 4 parallel Haiku analysts (Quantitative / Fundamental /
    Sentiment-Macro / Contrarian-Risk) followed by a Sonnet judge that
    synthesises into the final plan. Cost ~$0.10, latency ~30-45s.

    Cached per `(ticker, day, mode='deep')`; same shape as quick.
    """
    ticker = ticker.upper()
    if not force:
        cached = plan_cache.get_cached(session=session, ticker=ticker, mode="deep")
        if cached is not None:
            return cached

    try:
        packet = plan_context.gather(ticker=ticker, session=session)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to gather research packet: {e}"
        ) from e

    try:
        result = plan_deep.run(packet)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Deep research failed: {e}"
        ) from e

    if result.plan.lenses:
        plan_cache.store(session=session, plan=result.plan)
        session.commit()
    return result.plan


@router.get("/deep/{ticker}", response_model=EntryExitPlan | None)
def get_deep_research(
    ticker: str,
    session: Session = Depends(db_session),
) -> EntryExitPlan | None:
    """Return today's cached deep-mode plan for `ticker`, or null."""
    return plan_cache.get_cached(session=session, ticker=ticker.upper(), mode="deep")


@router.get("/lens-scorecards", response_model=list[dict])
def get_lens_scorecards(
    lookback_days: int = 90,
    horizon: str = "5d",
    session: Session = Depends(db_session),
) -> list[dict]:
    """Per-lens accuracy scorecards (Wilson CI, regime split, conviction split).

    Empty list until enough Deep/Quick runs accumulate. Phase 6 of the
    lens-agents roadmap injects these into the judge's prompt.
    """
    cards = compute_lens_scorecards(
        session, lookback_days=lookback_days, horizon=horizon
    )
    return [
        {
            "lens_name": c.lens_name,
            "horizon": c.horizon,
            "n": c.n,
            "hit_rate": c.hit_rate,
            "hit_rate_lo": c.hit_rate_lo,
            "hit_rate_hi": c.hit_rate_hi,
            "avg_excess_vs_spy": c.avg_excess_vs_spy,
            "by_regime": c.by_regime,
            "by_conviction": c.by_conviction,
            "by_direction": c.by_direction,
        }
        for c in cards
    ]


class ScanRerankRequest(BaseModel):
    """Body for POST /research/scan-rerank."""

    tickers: list[str] | None = Field(
        default=None,
        description="Explicit tickers to rerank. Ignored when source='watchlist'.",
    )
    source: str = Field(
        default="tickers",
        description="'tickers' (use body.tickers) or 'watchlist' (pull from Watchlist).",
    )


@router.post("/scan-rerank", response_model=list[dict])
def post_scan_rerank(
    body: ScanRerankRequest,
    session: Session = Depends(db_session),
) -> list[dict]:
    """Rerank a list of tickers via the reduced 2-lens panel + Sonnet judge.

    Body: `{"tickers": ["AAPL", "NVDA"]}` or `{"source": "watchlist"}` to pull
    from the user's pinned watchlist (entity_type='ticker').

    Cost: ~$0.01-0.015 per ticker. No caching this phase — every call
    spends. Use sparingly; user-driven, not cron-driven.
    """
    if body.source == "watchlist":
        rows = list(
            session.scalars(
                select(Watchlist).where(Watchlist.entity_type == "ticker")
            ).all()
        )
        ticker_list = [w.entity_id for w in rows]
    else:
        ticker_list = list(body.tickers or [])

    if not ticker_list:
        return []

    results = rerank_tickers(ticker_list)
    return [
        {
            "ticker": r.ticker,
            "as_of": r.as_of.isoformat(),
            "rank": r.rank,
            "rationale": r.rationale,
            "lenses": [
                {
                    "name": lv.name,
                    "direction": lv.direction,
                    "conviction": lv.conviction,
                    "summary": lv.summary,
                }
                for lv in r.lenses
            ],
            "cost_usd": r.cost_usd,
            "duration_ms": r.duration_ms,
            "sources_used": r.sources_used,
            "error": r.error,
        }
        for r in results
    ]
