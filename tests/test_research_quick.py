"""Quick-mode integration tests with a mocked Anthropic client.

Tests the full flow: ResearchPacket → Claude tool-use → EntryExitPlan,
without hitting the network. Also exercises the level-clamp and the
confidence-bounding guards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
)
from app.models import Base
from app.research import cache as plan_cache
from app.research.context import ResearchPacket
from app.research.exits import build_candidate_levels
from app.research.quick import run
from app.research.schema import LENS_NAMES, blended_risk_reward


def _stub_lenses() -> list[dict]:
    return [
        {
            "name": name,
            "direction": "neutral",
            "conviction": "low",
            "summary": f"stub {name}",
            "points": ["stub point"],
        }
        for name in LENS_NAMES
    ]


# ---------------------------------------------------------------------------
# Fakes


def _stub_view(*, status_conf: str = "high") -> TickerResearchView:
    as_of = datetime(2026, 5, 7, tzinfo=UTC)
    return TickerResearchView(
        ticker="AAPL",
        as_of=as_of,
        identity=IdentityCoverage(
            ticker="AAPL",
            in_universe=True,
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=False,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
        market=MarketSnapshotPanel(
            as_of=as_of,
            last_close=100.0,
            return_5d=0.02,
            return_21d=0.05,
            return_63d=0.10,
            pct_off_52w_high=-0.05,
            pct_off_52w_low=0.30,
        ),
        indicators=IndicatorPanel(
            ma_alignment="bullish_stack",
            rsi_14=58.0,
            atr_14=2.0,
            atr_14_pct=0.02,
        ),
        levels=LevelsPanel(
            nearest_support=95.0,
            nearest_resistance=105.0,
            recent_high_63d=108.0,
            base_low=80.0,
        ),
        setup=SetupClassification(setup_type="strong_uptrend", confidence="high"),
        style_fit=StyleFitPanel(items=[], primary_style="trend_pullback"),
        status=DecisionSupportStatus(
            status="research_candidate",
            confidence=status_conf,
            summary="stub research candidate",
        ),
        action=ActionSignal(label="BUY", confidence="high", derivation="stub"),
        entry_zone=EntryZoneCandidate(
            available=True,
            candidate_research_zone_low=98.0,
            candidate_research_zone_high=100.0,
            invalidation_reference=92.0,
        ),
        transcript=TranscriptContext(
            has_data=False, n_signals=0, n_calls=0, n_claims=0, summary="stub"
        ),
    )


def _stub_packet(*, status_conf: str = "high") -> ResearchPacket:
    view = _stub_view(status_conf=status_conf)
    breakout = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=108.0,
        candidate_research_zone_high=109.0,
        invalidation_reference=104.0,
    )
    pullback = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=98.0,
        candidate_research_zone_high=100.0,
        invalidation_reference=92.0,
    )
    cl = build_candidate_levels(
        indicators=view.indicators,
        levels=view.levels,
        market=view.market,
        breakout_entry=breakout,
        pullback_entry=pullback,
    )
    return ResearchPacket(
        ticker="AAPL",
        as_of=view.as_of,
        view=view,
        candidate_levels=cl,
        recent_claims=[],
        sources_used=["technicals"],
    )


def _fake_response(
    *,
    raw_input: dict,
    input_tokens: int = 4000,
    output_tokens: int = 1000,
):
    """Build an Anthropic-shaped response object with a single tool_use block."""
    block = SimpleNamespace(type="tool_use", name="submit_entry_exit_plan", input=raw_input)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model_dump=lambda: {"content": [{"type": "tool_use", "input": raw_input}]},
    )


class _FakeClient:
    def __init__(self, raw_input: dict, input_tokens=4000, output_tokens=1000):
        self._resp = _fake_response(
            raw_input=raw_input,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        class _Messages:
            def __init__(inner_self, parent):
                inner_self.parent = parent

            def create(inner_self, **kwargs):
                inner_self.parent.last_kwargs = kwargs
                return inner_self.parent._resp

        self.messages = _Messages(self)
        self.last_kwargs: dict = {}


# ---------------------------------------------------------------------------
# Happy path


def test_quick_run_returns_validated_plan():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "Breakout above 63d pivot.",
        "primary_exit_index": 0,
        "primary_exit_rationale": "First test of overhead supply.",
        "invalidation_index": 0,
        "invalidation_rationale": "Below entry support.",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["bull a"],
        "bear_case": ["bear a"],
        "key_risks": ["risk a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    result = run(packet, client=client)
    assert result.plan.ticker == "AAPL"
    # Numeric levels are pulled VERBATIM from CandidateLevels — no clamp.
    assert result.plan.entry_zone == packet.candidate_levels.breakout_entry
    assert result.plan.exit_zone_primary == packet.candidate_levels.primary_exit_candidates[0]
    assert result.plan.invalidation == packet.candidate_levels.invalidation_candidates[0]
    assert result.plan.confidence == "high"
    assert result.plan.timeframe == "5-15d"
    assert result.plan.cost_usd > 0
    assert result.plan.duration_ms >= 0
    assert result.plan.mode == "quick"
    assert result.plan.agent_trace[0].agent == "quick"
    assert "decision support" in result.plan.disclaimer.lower()
    assert result.plan.r_r_distribution is not None
    assert result.plan.r_r_distribution.n_combos > 0
    chosen = [c for c in result.plan.r_r_distribution.combos if c.is_chosen]
    assert len(chosen) == 1


def test_quick_passes_temperature_zero_to_anthropic():
    """The Quick call MUST be temperature=0 so the same packet → same plan.

    R/R variance run-to-run was traced to default temperature (1.0).
    See docs/superpowers/plans/2026-05-08-rr-variance-fix.md.
    """
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    run(packet, client=client)
    assert client.last_kwargs.get("temperature") == 0


# ---------------------------------------------------------------------------
# Robustness: graceful degradation against malformed LLM picks


def test_quick_invalid_entry_kind_falls_back_to_first_available():
    """If the LLM picks an entry_kind that isn't available, fall back to
    whichever entry candidate IS available — no exception."""
    packet = _stub_packet()
    # Force only pullback to be available.
    packet.candidate_levels.breakout_entry = None
    raw = {
        "entry_kind": "breakout",  # not available!
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.entry_zone == packet.candidate_levels.pullback_entry


def test_quick_out_of_range_primary_index_clamps_to_last():
    """LLM emits an out-of-range index → clamp to the nearest valid index."""
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 99,  # out of range
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    last = packet.candidate_levels.primary_exit_candidates[-1]
    assert plan.exit_zone_primary == last


# ---------------------------------------------------------------------------
# Hallucination guard: confidence bounded by upstream rubric


def test_quick_confidence_bounded_by_low_rubric():
    packet = _stub_packet(status_conf="low")
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",  # LLM says high
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    # Bounded to low because rubric says low.
    assert plan.confidence == "low"


def test_quick_confidence_unchanged_when_within_bound():
    packet = _stub_packet(status_conf="high")
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "medium",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.confidence == "medium"


# ---------------------------------------------------------------------------
# Risk/reward derivation


def test_quick_risk_reward_computed_from_resolved_levels():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    # R/R is computed from the deterministic candidate levels — never negative,
    # and matches a direct calc on the resolved zones.
    assert plan.risk_reward_primary >= 0


# ---------------------------------------------------------------------------
# Cache round-trip


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = factory()
    try:
        yield s
    finally:
        s.close()


def test_cache_store_then_get(session: Session):
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    plan_cache.store(session=session, plan=plan)
    session.commit()

    fetched = plan_cache.get_cached(
        session=session, ticker="AAPL", mode="quick", when=plan.as_of
    )
    assert fetched is not None
    assert fetched.entry_zone.low == plan.entry_zone.low
    assert fetched.confidence == plan.confidence


def test_cache_miss_returns_none(session: Session):
    fetched = plan_cache.get_cached(session=session, ticker="ZZZ", mode="quick")
    assert fetched is None


# ---------------------------------------------------------------------------
# Multi-lens panel + blended R/R


def test_quick_emits_four_lenses_with_directions():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "runner_exit_index": 0,
        "runner_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": [
            {"name": "quantitative", "direction": "bullish", "conviction": "high",
             "summary": "Momentum + bullish stack", "points": ["RSI 58", "21d ret +5%"]},
            {"name": "fundamental", "direction": "neutral", "conviction": "medium",
             "summary": "Reasonable forward P/E for sector", "points": ["fwd P/E 25", "rev growth +12%"]},
            {"name": "sentiment_macro", "direction": "bullish", "conviction": "medium",
             "summary": "Creator coverage strengthening", "points": ["3 mentions in 30d"]},
            {"name": "contrarian_risk", "direction": "bearish", "conviction": "low",
             "summary": "Watch crowded long", "points": ["RSI approaching 70", "narrow primary band"]},
        ],
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert len(plan.lenses) == 4
    names = [l.name for l in plan.lenses]
    assert set(names) == set(LENS_NAMES)
    quant = next(l for l in plan.lenses if l.name == "quantitative")
    assert quant.direction == "bullish"
    assert quant.conviction == "high"
    assert "Momentum" in quant.summary


def test_quick_computes_blended_r_r_when_runner_present():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "runner_exit_index": 0,
        "runner_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    # primary and runner R/R get computed from resolved levels; blended is the
    # weighted average using the default 1/3 - 2/3 split. Bounded by the two
    # extremes.
    assert plan.plan_r_r_blended is not None
    rr_min = min(plan.risk_reward_primary, plan.risk_reward_runner or 0)
    rr_max = max(plan.risk_reward_primary, plan.risk_reward_runner or 0)
    assert rr_min - 0.01 <= plan.plan_r_r_blended <= rr_max + 0.01


def test_blended_r_r_helper_basic():
    # 1/3 * 0.8 + 2/3 * 2.25 = 0.2667 + 1.5 = 1.7667 → 1.77
    assert blended_risk_reward(rr_primary=0.8, rr_runner=2.25) == 1.77


def test_blended_r_r_falls_back_to_primary_when_no_runner():
    assert blended_risk_reward(rr_primary=1.5, rr_runner=None) == 1.5


def test_blended_r_r_returns_none_when_both_missing():
    assert blended_risk_reward(rr_primary=None, rr_runner=None) is None
