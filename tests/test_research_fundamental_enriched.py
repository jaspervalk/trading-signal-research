"""Tests that the Fundamental agent threads the new enriched data into the prompt."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.analysis.schema import (
    ActionSignal,
    DecisionSupportStatus,
    EntryZoneCandidate,
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
    StyleFitPanel,
    TickerResearchView,
    TranscriptContext,
    ValuationPanel,
)
from app.market.fundamentals import FundamentalsExtended, RevenueTrajectory
from app.market.peer_comparison import PeerComparison
from app.research.agents.base import AgentResult
from app.research.agents.fundamental import run_fundamental
from app.research.context import ResearchPacket
from app.research.exits import build_candidate_levels
from app.research.schema import LensView


def _packet_with_enrichment() -> ResearchPacket:
    as_of = datetime(2026, 5, 28, tzinfo=UTC)
    view = TickerResearchView(
        ticker="NVDA",
        as_of=as_of,
        identity=IdentityCoverage(
            ticker="NVDA",
            in_universe=True,
            sector="Technology",
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=False,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
        market=MarketSnapshotPanel(as_of=as_of, last_close=180.0),
        valuation=ValuationPanel(
            sector="Technology", industry="Semiconductors",
            market_cap=4_200_000_000_000, forward_pe=18.0, peg_ratio=0.7,
            revenue_growth_yoy=0.65, profit_margins=0.55,
            days_to_next_earnings=21,
        ),
        indicators=IndicatorPanel(rsi_14=60, atr_14=4.0),
        levels=LevelsPanel(
            nearest_support=170.0, nearest_resistance=195.0,
            recent_high_63d=185.0, base_low=150.0,
        ),
        setup=SetupClassification(setup_type="uptrend_pullback", confidence="medium"),
        style_fit=StyleFitPanel(items=[], primary_style="trend_pullback"),
        status=DecisionSupportStatus(status="research_candidate", confidence="medium", summary="stub"),
        action=ActionSignal(label="ACCUMULATE", confidence="medium", derivation="stub"),
        entry_zone=EntryZoneCandidate(available=False),
        transcript=TranscriptContext(
            has_data=False, n_signals=0, n_calls=0, n_claims=0,
            coverage_status="none", n_distinct_creators=0,
            confirms_or_contradicts="neutral", summary="",
        ),
    )
    cl = build_candidate_levels(
        indicators=view.indicators, levels=view.levels, market=view.market,
        breakout_entry=EntryZoneCandidate(available=False),
        pullback_entry=EntryZoneCandidate(available=False),
    )

    fext = FundamentalsExtended(
        revenue=RevenueTrajectory(
            fy_minus_2=60_000_000_000,
            fy_minus_1=130_000_000_000,
            fy_minus_0=216_000_000_000,
        ),
        operating_margin_fy_minus_2=0.54,
        operating_margin_fy_minus_1=0.62,
        operating_margin_fy_minus_0=0.60,
        net_income_fy_minus_0=120_000_000_000,
        fcf_ttm=96_000_000_000, fcf_yield=0.023, capex_pct_revenue=0.028,
        current_ratio=3.9, debt_to_equity=0.07, total_cash=62_000_000_000,
        cash_to_market_cap=0.015, buyback_yield_ttm=0.0095,
        shares_outstanding_yoy_pct=-0.007,
        sources_used=["financials", "cashflow", "balance_sheet"],
    )

    pcmp = PeerComparison(
        peer_tickers=["AMD", "AVGO", "MU", "INTC", "QCOM"],
        peer_set_available=True,
        median_forward_pe=23.0, median_peg=1.06,
        median_revenue_growth_yoy=0.09, median_profit_margins=0.14,
        target_forward_pe=18.0, target_peg=0.7,
        target_revenue_growth_yoy=0.65, target_profit_margins=0.55,
    )

    return ResearchPacket(
        ticker="NVDA", as_of=as_of, view=view, candidate_levels=cl,
        recent_claims=[], sources_used=["technicals"],
        fundamentals_extended=fext, peer_comparison=pcmp,
        company_name="NVIDIA Corporation",
    )


def test_fundamental_prompt_includes_peer_comparison_and_trajectories():
    calls: dict[str, object] = {}

    def fake(**kwargs):
        calls.update(kwargs)
        return AgentResult(
            agent_name="fundamental",
            lens=LensView(
                name="fundamental", direction="bullish", conviction="high",
                summary="cheap relative to peers given growth", points=["p1", "p2"],
            ),
            cost_usd=0.01, duration_ms=200,
        )

    with patch("app.research.agents.fundamental.run_agent", side_effect=fake):
        run_fundamental(_packet_with_enrichment())

    msg = calls["user_message"]
    assert isinstance(msg, str)
    # Peer block surfaced.
    assert "PEER COMPARISON" in msg
    assert "AMD" in msg and "AVGO" in msg
    assert "23.0" in msg  # median forward PE
    # Revenue trajectory block.
    assert "REVENUE TRAJECTORY" in msg
    assert "growing" in msg  # quality read
    # Margin trajectory.
    assert "OPERATING MARGIN TRAJECTORY" in msg
    assert "expanding" in msg or "stable" in msg
    # Balance sheet.
    assert "BALANCE SHEET" in msg
    assert "current_ratio" in msg
    # Capital allocation.
    assert "CASH FLOW & CAPITAL ALLOCATION" in msg
    assert "FCF" in msg
    assert "Buyback yield" in msg
    # Company name surfaces.
    assert "NVIDIA Corporation" in msg


def test_fundamental_prompt_gracefully_handles_missing_enrichment():
    """When fundamentals_extended and peer_comparison are None (legacy path),
    the prompt should still render with "(not fetched)" or equivalent fallbacks
    rather than crashing."""
    pkt = _packet_with_enrichment()
    pkt.fundamentals_extended = None
    pkt.peer_comparison = None

    calls: dict[str, object] = {}

    def fake(**kwargs):
        calls.update(kwargs)
        return AgentResult(
            agent_name="fundamental",
            lens=LensView(
                name="fundamental", direction="neutral", conviction="low",
                summary="limited data", points=["p1", "p2"],
            ),
            cost_usd=0.005, duration_ms=150,
        )

    with patch("app.research.agents.fundamental.run_agent", side_effect=fake):
        run_fundamental(pkt)

    msg = calls["user_message"]
    assert "PEER COMPARISON" in msg
    assert "no peer comparison" in msg
    assert "REVENUE TRAJECTORY" in msg
    # Must NOT crash; falls back to neutral framing.
    assert "(not fetched)" in msg or "unavailable" in msg
