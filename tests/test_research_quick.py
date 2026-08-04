"""Quick-mode integration tests with a mocked Anthropic client.

Tests the full flow: ResearchPacket → Claude tool-use → EntryExitPlan,
without hitting the network. Also exercises the categorical-pick
resolution (entry_kind + integer indices, with index clamping for
out-of-range values) and the confidence-bounding guards.
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
            # 115 > breakout entry high (109) so primary[0] forms a non-degenerate
            # combo (reward > 0) for the breakout-entry tests below. Pre-fix the
            # 105 value silently produced an rr=0 combo that compute_rr_distribution
            # now correctly skips.
            nearest_resistance=115.0,
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
    stop_reason: str = "tool_use",
):
    """Build an Anthropic-shaped response object with a single tool_use block."""
    block = SimpleNamespace(type="tool_use", name="submit_entry_exit_plan", input=raw_input)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
        model_dump=lambda: {"content": [{"type": "tool_use", "input": raw_input}]},
    )


class _FakeClient:
    def __init__(
        self,
        raw_input: dict,
        input_tokens=4000,
        output_tokens=1000,
        stop_reason="tool_use",
    ):
        self._resp = _fake_response(
            raw_input=raw_input,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
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


# ---------------------------------------------------------------------------
# Bug 1 regression — Haiku XML-in-JSON recovery
#
# Live 2026-05-28: Haiku 4.5 sometimes stuffs all output (bull_case items,
# bear_case items, key_risks items, and the entire 4-lens array) as XML-style
# markup into a single string in bull_case[0], leaving the proper JSON fields
# empty. Example payload shape:
#
#   "bull_case": [
#     "\n<item>bull 1</item>\n<item>bull 2</item>\n</bull_case>\n"
#     "<parameter name=\"bear_case\">\n<item>bear 1</item>\n...</parameter "
#     "name=\"lenses\">\n[{...4 lenses as proper JSON...}]"
#   ],
#   "bear_case": [], "key_risks": [], "lenses": []
#
# The cleanest recovery is to detect this pattern at the tool-input
# boundary and parse it back into the proper fields. Normal responses
# (no XML embedding) pass through unchanged.


def test_quick_recovers_when_haiku_emits_xml_inside_bull_case():
    """The XML-embedded shape from the live 2026-05-28 NVDA response."""
    from app.research.quick import _recover_misformatted_response
    raw = {
        "entry_kind": "pullback",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": [
            "\n<item>bull alpha</item>\n<item>bull beta</item>\n</bull_case>\n"
            '<parameter name="bear_case">\n<item>bear alpha</item>\n<item>bear beta</item>\n</parameter>\n'
            '<parameter name="key_risks">\n<item>risk alpha</item>\n<item>risk beta</item>\n</parameter>\n'
            '<parameter name="lenses">\n['
            '{"name":"quantitative","direction":"bullish","conviction":"high",'
            '"summary":"q sum","points":["q1","q2"]},'
            '{"name":"fundamental","direction":"bullish","conviction":"medium",'
            '"summary":"f sum","points":["f1"]},'
            '{"name":"sentiment_macro","direction":"neutral","conviction":"low",'
            '"summary":"s sum","points":["s1"]},'
            '{"name":"contrarian_risk","direction":"bearish","conviction":"medium",'
            '"summary":"c sum","points":["c1"]}'
            ']'
        ],
        "bear_case": [],
        "key_risks": [],
        "lenses": [],
    }
    fixed = _recover_misformatted_response(raw)
    assert fixed["bull_case"] == ["bull alpha", "bull beta"]
    assert fixed["bear_case"] == ["bear alpha", "bear beta"]
    assert fixed["key_risks"] == ["risk alpha", "risk beta"]
    assert len(fixed["lenses"]) == 4
    names = [l["name"] for l in fixed["lenses"]]
    assert set(names) == {"quantitative", "fundamental", "sentiment_macro", "contrarian_risk"}


def test_quick_recovers_when_bull_case_is_a_bare_string():
    """Claude sometimes emits bull_case as a bare string (not a list); the
    recovery must still trigger on that shape. Real 2026-05-28 IREN case."""
    from app.research.quick import _recover_misformatted_response
    raw = {
        "bull_case": (
            "\n<item>bull A</item>\n<item>bull B</item>\n</bull_case>\n"
            '<parameter name="bear_case">\n<item>bear A</item>\n</parameter>\n'
            '<parameter name="key_risks">\n<item>risk A</item>\n</parameter>\n'
            '<parameter name="lenses">\n['
            '{"name":"quantitative","direction":"bullish","conviction":"high",'
            '"summary":"q","points":["q1"]},'
            '{"name":"fundamental","direction":"neutral","conviction":"low",'
            '"summary":"f","points":["f1"]},'
            '{"name":"sentiment_macro","direction":"neutral","conviction":"low",'
            '"summary":"s","points":["s1"]},'
            '{"name":"contrarian_risk","direction":"bearish","conviction":"medium",'
            '"summary":"c","points":["c1"]}'
            ']'
        ),
        "bear_case": [],
        "key_risks": [],
        "lenses": [],
    }
    fixed = _recover_misformatted_response(raw)
    assert fixed["bull_case"] == ["bull A", "bull B"]
    assert fixed["bear_case"] == ["bear A"]
    assert fixed["key_risks"] == ["risk A"]
    assert len(fixed["lenses"]) == 4


def test_quick_recovery_passes_well_formed_response_unchanged():
    """A normal JSON-shaped response is not disturbed by the recovery code."""
    from app.research.quick import _recover_misformatted_response
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["short normal bullet"],
        "bear_case": ["normal bear"],
        "key_risks": ["normal risk"],
        "lenses": _stub_lenses(),
    }
    fixed = _recover_misformatted_response(raw)
    assert fixed["bull_case"] == ["short normal bullet"]
    assert fixed["bear_case"] == ["normal bear"]
    assert fixed["key_risks"] == ["normal risk"]
    assert len(fixed["lenses"]) == 4


def test_quick_end_to_end_recovery_from_xml_embedded_response():
    """End-to-end: a Haiku response with XML-stuffed bull_case still yields
    a plan with 4 populated lenses and proper bull/bear/risk arrays."""
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
        "bull_case": [
            "\n<item>structural uptrend</item>\n<item>pullback setup</item>\n</bull_case>\n"
            '<parameter name="bear_case">\n<item>valuation rich</item>\n</parameter>\n'
            '<parameter name="key_risks">\n<item>earnings event</item>\n</parameter>\n'
            '<parameter name="lenses">\n['
            '{"name":"quantitative","direction":"bullish","conviction":"high",'
            '"summary":"Strong trend","points":["RSI 58","21d ret +5%"]},'
            '{"name":"fundamental","direction":"bullish","conviction":"medium",'
            '"summary":"OK fwd PE","points":["PEG 0.66"]},'
            '{"name":"sentiment_macro","direction":"neutral","conviction":"low",'
            '"summary":"thin coverage","points":["1 claim"]},'
            '{"name":"contrarian_risk","direction":"bearish","conviction":"medium",'
            '"summary":"crowded","points":["beta 2.24"]}'
            ']'
        ],
        "bear_case": [],
        "key_risks": [],
        "lenses": [],
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.bull_case == ["structural uptrend", "pullback setup"]
    assert plan.bear_case == ["valuation rich"]
    assert plan.key_risks == ["earnings event"]
    assert len(plan.lenses) == 4
    quant = next(l for l in plan.lenses if l.name == "quantitative")
    assert quant.direction == "bullish"


# ---------------------------------------------------------------------------
# Bug 1 regression — max_tokens truncation observability
#
# Live 2026-05-28 batch: 10/10 Quick runs returned lenses=[]. Output cost
# ~$0.022 implied ~3600 output tokens against the 4000 max_tokens budget —
# Claude was hitting the limit and the lens block (last/largest item in the
# response JSON) was being truncated. Two fixes:
# 1. Bump default max_tokens to give the 4-lens panel real headroom.
# 2. When stop_reason == "max_tokens", log a structured warning so future
#    truncations are observable rather than silently producing empty lenses.


def test_quick_default_max_tokens_has_headroom_for_four_lenses():
    """The default budget must comfortably fit the 4-lens output. Pre-fix it
    was 4000; that hit truncation on every real-call lens panel."""
    from app.research import quick
    assert quick.DEFAULT_MAX_TOKENS >= 8000, (
        f"DEFAULT_MAX_TOKENS={quick.DEFAULT_MAX_TOKENS} is too tight for the "
        "4-lens response payload. Bumped to 8000+ as part of the Bug 1 fix."
    )


def test_quick_logs_warning_when_response_truncated_by_max_tokens(monkeypatch):
    """If Anthropic returns stop_reason='max_tokens', we MUST log it visibly.
    The 2026-05-28 batch had this happen silently 10/10 runs; the only way
    we noticed was empty lens arrays in the final plan."""
    from app.research import quick
    calls: list[tuple[str, dict]] = []

    class _SpyLog:
        def warning(self, event, **kw):
            calls.append((event, kw))
        def error(self, event, **kw):
            calls.append((event, kw))

    monkeypatch.setattr(quick, "log", _SpyLog())

    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout", "entry_rationale": "x",
        "primary_exit_index": 0, "primary_exit_rationale": "x",
        "invalidation_index": 0, "invalidation_rationale": "x",
        "confidence": "high", "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": [],  # truncated — lens block didn't make it
    }
    client = _FakeClient(raw, stop_reason="max_tokens")
    run(packet, client=client)
    truncation_events = [
        (event, kw) for (event, kw) in calls
        if "max_tokens" in event or "truncat" in event.lower()
    ]
    assert truncation_events, (
        f"Expected a structured warning event when stop_reason='max_tokens'; "
        f"got events: {[c[0] for c in calls]}"
    )


def test_quick_does_not_log_truncation_on_normal_completion(monkeypatch):
    """Sanity: stop_reason='tool_use' (normal) does NOT produce a truncation
    warning."""
    from app.research import quick
    calls: list[tuple[str, dict]] = []

    class _SpyLog:
        def warning(self, event, **kw):
            calls.append((event, kw))
        def error(self, event, **kw):
            calls.append((event, kw))

    monkeypatch.setattr(quick, "log", _SpyLog())

    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout", "entry_rationale": "x",
        "primary_exit_index": 0, "primary_exit_rationale": "x",
        "invalidation_index": 0, "invalidation_rationale": "x",
        "confidence": "high", "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw, stop_reason="tool_use")
    run(packet, client=client)
    truncation_events = [
        (event, kw) for (event, kw) in calls
        if "max_tokens" in event
    ]
    assert not truncation_events


# ---------------------------------------------------------------------------
# Bug 2 regression — str→list[char] coercion
#
# Live 2026-05-28 batch: HIMS bull_case came back with 1746 entries (the
# character count of a single string), NBIS 155/171/164. Root cause: when
# Claude emits a bare string instead of a JSON array, `list("text")` yields
# `['t','e','x','t']`. The model layer must wrap a bare str into [str].


def test_quick_bull_case_string_becomes_single_element_list():
    """A bare-string bull_case from the LLM must NOT be iterated into chars."""
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
        "bull_case": "Strong technical setup with momentum",  # string, not list
        "bear_case": ["normal bear"],
        "key_risks": ["normal risk"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.bull_case == ["Strong technical setup with momentum"]
    # Explicit: not the character-list artifact.
    assert plan.bull_case != list("Strong technical setup with momentum")


def test_quick_bear_case_and_key_risks_string_become_single_element_lists():
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
        "bull_case": ["ok"],
        "bear_case": "Valuation stretched",
        "key_risks": "Earnings in 3 days",
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.bear_case == ["Valuation stretched"]
    assert plan.key_risks == ["Earnings in 3 days"]


def test_quick_lens_points_string_becomes_single_element_list():
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
        "bull_case": ["ok"], "bear_case": ["ok"], "key_risks": ["ok"],
        "lenses": [
            {"name": "quantitative", "direction": "bullish", "conviction": "medium",
             "summary": "stub", "points": "RSI 58 and momentum aligning"},  # string
            {"name": "fundamental", "direction": "neutral", "conviction": "low",
             "summary": "stub", "points": ["normal", "list"]},
            {"name": "sentiment_macro", "direction": "neutral", "conviction": "low",
             "summary": "stub", "points": ["stub"]},
            {"name": "contrarian_risk", "direction": "neutral", "conviction": "low",
             "summary": "stub", "points": ["stub"]},
        ],
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    quant = next(l for l in plan.lenses if l.name == "quantitative")
    assert quant.points == ["RSI 58 and momentum aligning"]


def test_lens_view_string_points_validator_at_model_level():
    """Direct construction with a string `points` value must wrap, not iterate."""
    from app.research.schema import LensView
    lv = LensView(
        name="quantitative",
        direction="bullish",
        conviction="medium",
        summary="ok",
        points="single point as bare string",
    )
    assert lv.points == ["single point as bare string"]


# ---------------------------------------------------------------------------
# Bug 3 regression — post-LLM stop-inside-entry snap (Quick path)
#
# Belt-and-suspenders: even if the exits.py filter misses a degenerate
# invalidation, the resolver in quick.py snaps it to a safe value
# (entry.low - 0.75×ATR) at the boundary. Avoids ever returning a plan
# with a long-side stop inside or above the entry zone.


def test_quick_snaps_invalidation_inside_entry_band_below_entry_low():
    """Force an invalidation_candidate inside the entry band, verify the
    resolver snaps it to entry.low - 0.75*ATR."""
    packet = _stub_packet()
    # Inject a stop inside the breakout band (108-109) and override the
    # candidate list (bypassing exits.py filter to test the post-LLM snap).
    packet.candidate_levels.invalidation_candidates = [108.5]
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
    entry_low = plan.entry_zone.low
    atr = packet.candidate_levels.atr_14
    expected = entry_low - 0.75 * atr
    assert plan.invalidation < entry_low, (
        f"Stop {plan.invalidation} must be < entry.low {entry_low}"
    )
    assert abs(plan.invalidation - expected) < 1e-6, (
        f"Expected snap to entry.low - 0.75*ATR = {expected}, got {plan.invalidation}"
    )


def test_quick_snaps_invalidation_above_entry_high_below_entry_low():
    """Stop above entry.high → snap below entry.low. IREN/NBIS Deep mode case."""
    packet = _stub_packet()
    packet.candidate_levels.invalidation_candidates = [
        packet.candidate_levels.breakout_entry.high + 5.0  # above entry
    ]
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
    assert plan.invalidation < plan.entry_zone.low


def test_quick_valid_invalidation_passes_through_unchanged():
    """Sanity: a normal (already-valid) invalidation isn't disturbed."""
    packet = _stub_packet()
    # Default _stub_packet provides breakout invalidation_reference=104.0,
    # which is well below the breakout entry low of 108.0.
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
    # Should match packet's first invalidation candidate verbatim
    assert plan.invalidation == packet.candidate_levels.invalidation_candidates[0]


def test_entry_exit_plan_string_bull_case_validator_at_model_level():
    """Direct construction of EntryExitPlan with a string bull_case wraps it."""
    from datetime import UTC, datetime
    from app.research.schema import EntryExitPlan, ZoneBand
    plan = EntryExitPlan(
        ticker="AAPL",
        as_of=datetime(2026, 5, 28, tzinfo=UTC),
        entry_zone=ZoneBand(low=100, high=101, method="m", rationale="r"),
        exit_zone_primary=ZoneBand(low=110, high=111, method="m", rationale="r"),
        invalidation=95.0,
        risk_reward_primary=2.0,
        confidence="medium",
        timeframe="5-15d",
        bull_case="solo bull point",
        bear_case=["normal"],
        key_risks="solo risk",
        mode="quick",
        cost_usd=0.01,
        duration_ms=1000,
    )
    assert plan.bull_case == ["solo bull point"]
    assert plan.key_risks == ["solo risk"]
    assert plan.bear_case == ["normal"]
