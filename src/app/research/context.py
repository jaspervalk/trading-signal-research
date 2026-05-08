"""Gather all inputs the LLM needs for an entry/exit plan.

A `ResearchPacket` bundles the deterministic technicals + recent creator
claims into one structure. The Quick path passes this packet to a single
Claude call; the Deep path (Phase 2) hands subsets to specialised analysts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.entry import build_dual_entry_zones
from app.analysis.research import build_ticker_research_view
from app.analysis.schema import TickerResearchView
from app.models import CLAIM_STATUS_ACCEPTED, Claim, Creator, Document, SourceChannel
from app.research.exits import build_candidate_levels
from app.research.schema import CandidateLevels


@dataclass
class ClaimSnippet:
    """A trimmed-down claim for inclusion in the LLM prompt.

    We don't pass the full ORM row — just the fields the LLM needs to weigh
    sentiment without burning tokens on noise.
    """

    claim_type: str
    polarity: str
    claim_class: str
    summary: str
    evidence_quote: str
    creator_name: str | None
    posted_at: datetime
    final_confidence: float


@dataclass
class ResearchPacket:
    """All inputs assembled for one ticker, one timestamp."""

    ticker: str
    as_of: datetime
    view: TickerResearchView
    candidate_levels: CandidateLevels
    recent_claims: list[ClaimSnippet] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)


CLAIMS_LOOKBACK_DAYS = 30
CLAIMS_MAX_ROWS = 12  # cap to keep token usage predictable


def gather(
    ticker: str,
    *,
    session: Session,
    as_of: datetime | None = None,
    view: TickerResearchView | None = None,
) -> ResearchPacket:
    """Build a `ResearchPacket` for `ticker`.

    `view` is optional — pass an already-built `TickerResearchView` to avoid
    a redundant yfinance fetch. Otherwise this calls
    `build_ticker_research_view` itself.
    """
    ticker = ticker.upper()
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    if view is None:
        # Fetch metadata so the Fundamental lens has valuation context
        # (forward P/E, growth, beta, etc.). Cost is one yfinance.get_info()
        # call, ~250ms cold, free on the lru_cache.
        view = build_ticker_research_view(
            ticker, session=session, as_of=as_of, fetch_metadata=True
        )

    breakout, pullback = build_dual_entry_zones(
        view.setup, view.indicators, view.levels, view.market
    )
    candidate_levels = build_candidate_levels(
        indicators=view.indicators,
        levels=view.levels,
        market=view.market,
        breakout_entry=breakout,
        pullback_entry=pullback,
    )

    recent_claims = _recent_claims(session=session, ticker=ticker, as_of=as_of)

    sources = ["technicals"]
    if recent_claims:
        sources.append("claims")
    return ResearchPacket(
        ticker=ticker,
        as_of=as_of,
        view=view,
        candidate_levels=candidate_levels,
        recent_claims=recent_claims,
        sources_used=sources,
    )


def _recent_claims(
    *, session: Session, ticker: str, as_of: datetime
) -> list[ClaimSnippet]:
    """Return up to `CLAIMS_MAX_ROWS` accepted claims for `ticker` posted in
    the last `CLAIMS_LOOKBACK_DAYS`. Most recent first.

    Document → Creator is a 2-hop relation: Document.source_channel_id →
    SourceChannel.creator_id → Creator. We left-join through both so claims
    from documents without a resolved creator still surface (creator_name=None).
    """
    cutoff = as_of - timedelta(days=CLAIMS_LOOKBACK_DAYS)
    stmt = (
        select(Claim, Document, Creator.display_name)
        .join(Document, Claim.document_id == Document.id)
        .outerjoin(SourceChannel, Document.source_channel_id == SourceChannel.id)
        .outerjoin(Creator, SourceChannel.creator_id == Creator.id)
        .where(Claim.ticker == ticker)
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
        .where(Document.posted_at >= cutoff)
        .order_by(Document.posted_at.desc())
        .limit(CLAIMS_MAX_ROWS)
    )
    rows = session.execute(stmt).all()

    out: list[ClaimSnippet] = []
    for claim, doc, creator_name in rows:
        out.append(
            ClaimSnippet(
                claim_type=claim.claim_type,
                polarity=claim.polarity,
                claim_class=claim.claim_class,
                summary=claim.summary,
                evidence_quote=claim.evidence_quote,
                creator_name=creator_name,
                posted_at=doc.posted_at,
                final_confidence=claim.final_confidence,
            )
        )
    return out


__all__ = ["ClaimSnippet", "ResearchPacket", "gather"]
