"""Tests for the Sentiment-Macro agent's web_search wiring.

We don't hit the network — we patch `run_agent_with_web_search` (and
the simpler `run_agent`) and inspect what they were called with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
from app.research.agents.base import (
    AgentResult,
    _extract_search_citations,
    _extract_web_search_usage,
)
from app.research.agents.sentiment import run_sentiment
from app.research.context import ResearchPacket
from app.research.exits import build_candidate_levels
from app.research.schema import LensView


def _view() -> TickerResearchView:
    as_of = datetime(2026, 5, 28, tzinfo=UTC)
    return TickerResearchView(
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
            sector="Technology",
            industry="Semiconductors",
            forward_pe=18.0,
            beta=1.7,
            days_to_next_earnings=21,
        ),
        indicators=IndicatorPanel(rsi_14=60, atr_14=4.0),
        levels=LevelsPanel(
            nearest_support=170.0,
            nearest_resistance=195.0,
            recent_high_63d=185.0,
            base_low=150.0,
        ),
        setup=SetupClassification(setup_type="uptrend_pullback", confidence="medium"),
        style_fit=StyleFitPanel(items=[], primary_style="trend_pullback"),
        status=DecisionSupportStatus(
            status="research_candidate", confidence="medium", summary="stub"
        ),
        action=ActionSignal(label="ACCUMULATE", confidence="medium", derivation="stub"),
        entry_zone=EntryZoneCandidate(available=False),
        transcript=TranscriptContext(
            has_data=False, n_signals=0, n_calls=0, n_claims=0,
            coverage_status="none", n_distinct_creators=0,
            confirms_or_contradicts="neutral", summary="",
        ),
    )


def _packet() -> ResearchPacket:
    v = _view()
    cl = build_candidate_levels(
        indicators=v.indicators, levels=v.levels, market=v.market,
        breakout_entry=EntryZoneCandidate(available=False),
        pullback_entry=EntryZoneCandidate(available=False),
    )
    return ResearchPacket(
        ticker="NVDA",
        as_of=v.as_of,
        view=v,
        candidate_levels=cl,
        recent_claims=[],
        sources_used=["technicals"],
        company_name="NVIDIA Corporation",
    )


def test_sentiment_uses_web_search_runner_when_enabled():
    """When sentiment_web_search=True, run_sentiment should hit the
    web_search runner with the ticker+company query embedded in the prompt."""
    calls: dict[str, object] = {}

    def fake_runner(**kwargs):
        calls.update(kwargs)
        return AgentResult(
            agent_name="sentiment_macro",
            lens=LensView(
                name="sentiment_macro", direction="bullish", conviction="medium",
                summary="stub", points=["p1", "p2"],
            ),
            cost_usd=0.02, duration_ms=300,
        )

    from app.config import DeepResearchSettings
    fake_settings = SimpleNamespace(
        research=SimpleNamespace(
            deep=DeepResearchSettings(
                sentiment_web_search=True, sentiment_web_search_max_uses=1,
            )
        )
    )

    with patch("app.research.agents.sentiment.run_agent_with_web_search",
               side_effect=fake_runner), \
         patch("app.research.agents.sentiment.load_project_settings",
               return_value=fake_settings):
        result = run_sentiment(_packet())

    assert result.lens is not None
    assert calls["agent_name"] == "sentiment_macro"
    assert calls["web_search_max_uses"] == 1
    user_msg = calls["user_message"]
    assert isinstance(user_msg, str)
    assert "NVDA" in user_msg
    assert "NVIDIA Corporation" in user_msg
    assert "news outlook 2026" in user_msg
    assert "web_search" in user_msg.lower()


def test_sentiment_falls_back_to_no_search_runner_when_disabled():
    """When sentiment_web_search=False, plain run_agent is used and no
    search-hint block appears in the prompt."""
    calls: dict[str, object] = {}

    def fake_runner(**kwargs):
        calls.update(kwargs)
        return AgentResult(
            agent_name="sentiment_macro",
            lens=LensView(
                name="sentiment_macro", direction="neutral", conviction="low",
                summary="stub", points=["p1", "p2"],
            ),
            cost_usd=0.005, duration_ms=200,
        )

    from app.config import DeepResearchSettings
    fake_settings = SimpleNamespace(
        research=SimpleNamespace(
            deep=DeepResearchSettings(sentiment_web_search=False)
        )
    )

    with patch("app.research.agents.sentiment.run_agent", side_effect=fake_runner), \
         patch("app.research.agents.sentiment.load_project_settings",
               return_value=fake_settings):
        result = run_sentiment(_packet())

    assert result.lens is not None
    user_msg = calls["user_message"]
    assert "web_search disabled" in user_msg


def test_extract_web_search_usage_counts_server_tool_blocks():
    """If usage.server_tool_use is missing, fall back to content-block counting."""
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        content=[
            SimpleNamespace(type="server_tool_use", name="web_search"),
            SimpleNamespace(type="web_search_tool_result", content=[
                SimpleNamespace(url="https://example.com/a", title="Article A"),
            ]),
            SimpleNamespace(type="tool_use", name="submit_lens", input={"direction": "neutral"}),
        ],
    )
    n, cost = _extract_web_search_usage(response)
    assert n == 1
    assert cost == pytest.approx(0.01)

    cites = _extract_search_citations(response)
    assert cites == [{"title": "Article A", "url": "https://example.com/a"}]


def test_extract_web_search_usage_prefers_usage_field():
    """If the SDK populates usage.server_tool_use, that takes precedence."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=500,
            server_tool_use=SimpleNamespace(web_search_requests=3),
        ),
        content=[],
    )
    n, cost = _extract_web_search_usage(response)
    assert n == 3
    assert cost == pytest.approx(0.03)
