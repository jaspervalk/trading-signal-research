"""Deep-mode multi-agent tests with mocked Anthropic clients.

Covers:
- `run_agent` happy path + tool-use parsing
- `run_agent` failure modes (no tool block, schema parse error)
- `run_agents_parallel` order + failure isolation
- `run_judge` clamping + min-risk-distance + confidence bounding
- `deep.run` end-to-end orchestration
- `deep.run` partial-failure tolerance (3-of-4 lenses succeed)
- `deep.run` total-failure raises
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

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
from app.research import deep as plan_deep
from app.research.agents import judge as judge_mod
from app.research.agents.base import (
    AgentResult,
    run_agent,
    run_agents_parallel,
)
from app.research.context import ResearchPacket
from app.research.exits import build_candidate_levels
from app.research.schema import LENS_NAMES, LensView


# ---------------------------------------------------------------------------
# Fixtures


def _stub_view() -> TickerResearchView:
    as_of = datetime(2026, 5, 8, tzinfo=UTC)
    return TickerResearchView(
        ticker="AAPL",
        as_of=as_of,
        identity=IdentityCoverage(
            ticker="AAPL",
            in_universe=True,
            sector="Technology",
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=False,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
        market=MarketSnapshotPanel(
            as_of=as_of,
            last_close=287.0,
            return_5d=0.06,
            return_21d=0.11,
            return_63d=0.04,
            pct_off_52w_high=-0.01,
            pct_off_52w_low=0.34,
        ),
        valuation=ValuationPanel(
            sector="Technology",
            industry="Consumer Electronics",
            market_cap=4_200_000_000_000,
            forward_pe=30.1,
            trailing_pe=34.8,
            peg_ratio=2.52,
            price_to_sales_ttm=9.4,
            earnings_growth_forward=0.218,
            revenue_growth_yoy=0.166,
            profit_margins=0.272,
            beta=1.06,
            short_pct_of_float=0.0092,
            days_to_next_earnings=83,
        ),
        indicators=IndicatorPanel(
            ma_alignment="bullish_stack",
            rsi_14=67.0,
            atr_14=6.7,
            atr_14_pct=0.0233,
            sma_50_slope_21d_pct=0.018,
            sma_200_slope_63d_pct=0.024,
        ),
        levels=LevelsPanel(
            nearest_support=270.0,
            # 298 > breakout entry high (290.86) so primary[0] forms a non-degenerate
            # combo (reward > 0) for the breakout-entry tests. Pre-fix this was
            # 288.35, silently producing an rr=0 combo that compute_rr_distribution
            # now correctly skips.
            nearest_resistance=298.0,
            recent_high_63d=287.51,
            base_low=245.51,
        ),
        setup=SetupClassification(setup_type="breakout_candidate", confidence="high"),
        style_fit=StyleFitPanel(items=[], primary_style="momentum_breakout"),
        status=DecisionSupportStatus(
            status="research_candidate",
            confidence="high",
            summary="stub research candidate",
        ),
        action=ActionSignal(label="ACCUMULATE", confidence="high", derivation="stub"),
        entry_zone=EntryZoneCandidate(
            available=True,
            candidate_research_zone_low=287.51,
            candidate_research_zone_high=290.86,
            invalidation_reference=277.48,
        ),
        transcript=TranscriptContext(
            has_data=True,
            n_signals=1,
            n_calls=0,
            n_claims=3,
            coverage_status="fresh",
            n_distinct_creators=1,
            confirms_or_contradicts="confirms",
            summary="stub",
            days_since_most_recent=2,
        ),
    )


def _stub_packet() -> ResearchPacket:
    view = _stub_view()
    breakout = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=287.51,
        candidate_research_zone_high=290.86,
        invalidation_reference=277.48,
    )
    pullback = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=262.40,
        candidate_research_zone_high=272.59,
        invalidation_reference=256.0,
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


def _fake_response(*, tool_name: str, raw: dict, in_tok: int = 3000, out_tok: int = 800):
    block = SimpleNamespace(type="tool_use", name=tool_name, input=raw)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        model_dump=lambda: {"content": [{"type": "tool_use", "input": raw}]},
    )


class _FakeClient:
    """Anthropic-shaped client that returns a canned tool response per call.

    Records kwargs from each `messages.create()` call:
    - `last_kwargs` — most recent call's kwargs (any tool)
    - `kwargs_by_tool` — kwargs keyed by the tool name in the call
    """

    def __init__(self, mapping: dict[str, dict[str, Any]]):
        self._mapping = mapping
        self.last_kwargs: dict[str, Any] = {}
        self.kwargs_by_tool: dict[str, dict[str, Any]] = {}

        class _Messages:
            def __init__(inner, parent):
                inner.parent = parent

            def create(inner, **kwargs):
                tool = kwargs["tools"][0]["name"]
                inner.parent.last_kwargs = kwargs
                inner.parent.kwargs_by_tool[tool] = kwargs
                # The judge tool is named differently from the analyst tool;
                # we look up by tool name. If unmapped, return a default lens.
                raw = inner.parent._mapping.get(tool)
                if raw is None:
                    raw = inner.parent._mapping.get("default", {})
                return _fake_response(tool_name=tool, raw=raw)

        self.messages = _Messages(self)


def _good_lens(direction: str = "bullish", conviction: str = "medium") -> dict:
    return {
        "direction": direction,
        "conviction": conviction,
        "summary": "stub summary",
        "points": ["point 1", "point 2"],
    }


def _good_judge() -> dict:
    return {
        "entry_kind": "breakout",
        "entry_rationale": "breakout band picked on momentum stack",
        "primary_exit_index": 0,
        "primary_exit_rationale": "fib 1.272× nearest target",
        "runner_exit_index": 0,
        "runner_exit_rationale": "fib 1.618× runner",
        "invalidation_index": 0,
        "invalidation_rationale": "swing low under base",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["per Quant: trend intact"],
        "bear_case": ["per Contrarian: RSI elevated"],
        "key_risks": ["earnings 83d"],
    }


# ---------------------------------------------------------------------------
# run_agent — happy path + failure modes


def test_run_agent_happy_path():
    client = _FakeClient({"submit_lens": _good_lens(direction="bullish", conviction="high")})
    result = run_agent(
        agent_name="quantitative",
        system_prompt="sys",
        user_message="user",
        client=client,
    )
    assert result.lens is not None
    assert result.lens.name == "quantitative"
    assert result.lens.direction == "bullish"
    assert result.lens.conviction == "high"
    assert result.cost_usd > 0
    assert result.error is None


def test_run_agent_returns_error_when_no_tool_block():
    class _NoToolClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                # Response has only a text block, no tool_use.
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="no tool")],
                    usage=SimpleNamespace(input_tokens=100, output_tokens=10),
                    model_dump=lambda: {},
                )

    client = _NoToolClient()
    result = run_agent(
        agent_name="quantitative",
        system_prompt="sys",
        user_message="user",
        client=client,
    )
    assert result.lens is None
    assert "no tool_use" in (result.error or "")


def test_run_agent_returns_error_when_api_raises():
    class _ExplodingClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("API down")

    result = run_agent(
        agent_name="quantitative",
        system_prompt="sys",
        user_message="user",
        client=_ExplodingClient(),
    )
    assert result.lens is None
    assert "API down" in (result.error or "")


# ---------------------------------------------------------------------------
# run_agents_parallel


def test_run_agents_parallel_returns_in_order():
    def make_runner(name: str, conviction: str):
        def _r() -> AgentResult:
            return AgentResult(
                agent_name=name,
                lens=LensView(
                    name=name, direction="bullish", conviction=conviction,
                    summary="x", points=["p"],
                ),
                cost_usd=0.01,
                duration_ms=100,
            )
        return _r

    runners = [
        make_runner("quantitative", "high"),
        make_runner("fundamental", "medium"),
        make_runner("sentiment_macro", "medium"),
        make_runner("contrarian_risk", "low"),
    ]
    out = run_agents_parallel(runners)
    assert len(out) == 4
    assert [r.agent_name for r in out] == [
        "quantitative", "fundamental", "sentiment_macro", "contrarian_risk",
    ]


def test_run_agents_parallel_isolates_failures():
    def good() -> AgentResult:
        return AgentResult(
            agent_name="quantitative",
            lens=LensView(name="quantitative", direction="bullish",
                          conviction="medium", summary="", points=["p"]),
            cost_usd=0.01, duration_ms=10,
        )

    def bad() -> AgentResult:
        return AgentResult(
            agent_name="contrarian_risk", lens=None, cost_usd=0.0,
            duration_ms=10, error="simulated",
        )

    out = run_agents_parallel([good, bad])
    assert len(out) == 2
    assert out[0].lens is not None
    assert out[1].lens is None
    assert out[1].error == "simulated"


# ---------------------------------------------------------------------------
# run_judge — clamping, confidence bounding, R/R math


def test_judge_emits_plan_with_lenses_and_blended_rr():
    packet = _stub_packet()
    lenses = [
        LensView(name=n, direction="bullish", conviction="medium",
                 summary=f"stub {n}", points=["p1", "p2"])
        for n in LENS_NAMES
    ]
    analyst_results = [
        AgentResult(agent_name=n, lens=l, cost_usd=0.01, duration_ms=200)
        for n, l in zip(LENS_NAMES, lenses)
    ]
    client = _FakeClient({"submit_synthesis": _good_judge()})
    result = judge_mod.run_judge(
        packet=packet, lenses=lenses, analyst_results=analyst_results, client=client,
    )
    plan = result.plan
    assert plan.mode == "deep"
    assert len(plan.lenses) == 4
    assert plan.plan_r_r_blended is not None
    assert plan.cost_usd > 0
    # Cost should sum analyst costs ($0.04) + judge cost (>0).
    assert plan.cost_usd >= 0.04
    # Trace = 4 analysts + 1 judge.
    assert len(plan.agent_trace) == 5
    assert plan.agent_trace[-1].agent == "judge"
    # The judge picks levels by reference, so picked levels must equal
    # candidate-list entries verbatim.
    cl = packet.candidate_levels
    assert plan.entry_zone == cl.breakout_entry
    assert plan.exit_zone_primary == cl.primary_exit_candidates[0]
    assert plan.invalidation == cl.invalidation_candidates[0]
    # R/R distribution must populate.
    assert plan.r_r_distribution is not None
    assert plan.r_r_distribution.n_combos > 0
    chosen = [c for c in plan.r_r_distribution.combos if c.is_chosen]
    assert len(chosen) == 1


def test_judge_invalid_entry_kind_falls_back():
    packet = _stub_packet()
    raw = dict(_good_judge())
    raw["entry_kind"] = "rocketship"  # not in {"breakout","pullback"}
    client = _FakeClient({"submit_synthesis": raw})
    plan = judge_mod.run_judge(
        packet=packet, lenses=[], analyst_results=[], client=client,
    ).plan
    # Falls back to breakout (default), which is available in the stub packet.
    assert plan.entry_zone == packet.candidate_levels.breakout_entry


def test_judge_out_of_range_primary_index_clamps():
    packet = _stub_packet()
    raw = dict(_good_judge())
    raw["primary_exit_index"] = 999  # way past end
    client = _FakeClient({"submit_synthesis": raw})
    plan = judge_mod.run_judge(
        packet=packet, lenses=[], analyst_results=[], client=client,
    ).plan
    cl = packet.candidate_levels
    # Clamps to last available primary candidate.
    assert plan.exit_zone_primary == cl.primary_exit_candidates[-1]


def test_judge_passes_temperature_zero_to_anthropic():
    """The Judge synthesis call MUST be temperature=0 so the same lens panel
    → same plan numerics. Lens analysts keep default temperature — divergent
    reads are their job. See docs/superpowers/plans/2026-05-08-rr-variance-fix.md.
    """
    packet = _stub_packet()
    client = _FakeClient({"submit_synthesis": _good_judge()})
    judge_mod.run_judge(
        packet=packet, lenses=[], analyst_results=[], client=client,
    )
    judge_kwargs = client.kwargs_by_tool.get("submit_synthesis")
    assert judge_kwargs is not None
    assert judge_kwargs.get("temperature") == 0


def test_judge_bounds_confidence_to_rubric():
    packet = _stub_packet()
    # Force rubric to low.
    packet.view.status.confidence = "low"
    raw = dict(_good_judge())
    raw["confidence"] = "high"
    client = _FakeClient({"submit_synthesis": raw})
    plan = judge_mod.run_judge(
        packet=packet, lenses=[], analyst_results=[], client=client,
    ).plan
    assert plan.confidence == "low"


# ---------------------------------------------------------------------------
# deep.run — orchestration


def test_deep_run_end_to_end():
    packet = _stub_packet()
    client = _FakeClient(
        {
            "submit_lens": _good_lens(direction="bullish", conviction="medium"),
            "submit_synthesis": _good_judge(),
        }
    )
    result = plan_deep.run(packet, client=client)
    plan = result.plan
    assert plan.mode == "deep"
    # Four parallel analysts + judge → 4 lenses on the plan.
    assert len(plan.lenses) == 4
    # Each analyst's lens should land in the plan.
    assert {l.name for l in plan.lenses} == set(LENS_NAMES)
    # All 4 analysts ran.
    assert len(result.analyst_results) == 4
    # Cost is summed across 5 LLM calls; lower bound is 4 × small + judge.
    assert plan.cost_usd > 0


def test_deep_run_tolerates_partial_failures():
    packet = _stub_packet()

    # First three analysts succeed, contrarian fails. We simulate by making the
    # client return a malformed lens for the 4th call. To do that without a
    # complex per-call counter, we override the contrarian agent module-level.
    import app.research.deep as deep_mod
    original_contrarian = deep_mod.run_contrarian

    def failing_contrarian(p, client=None):
        return AgentResult(
            agent_name="contrarian_risk",
            lens=None,
            cost_usd=0.0,
            duration_ms=50,
            error="forced failure for test",
        )

    deep_mod.run_contrarian = failing_contrarian
    try:
        client = _FakeClient(
            {
                "submit_lens": _good_lens(direction="bullish", conviction="medium"),
                "submit_synthesis": _good_judge(),
            }
        )
        result = plan_deep.run(packet, client=client)
    finally:
        deep_mod.run_contrarian = original_contrarian

    # 3 lenses survive; judge still produces a plan.
    assert len(result.plan.lenses) == 3
    assert "contrarian_risk" not in {l.name for l in result.plan.lenses}
    # Trace still records all 4 analysts (failed one as a NOTE), plus judge.
    assert len(result.plan.agent_trace) == 5
    failed_trace = next(
        n for n in result.plan.agent_trace if n.agent == "contrarian_risk"
    )
    assert "FAILED" in failed_trace.note


def test_deep_run_raises_when_all_analysts_fail():
    packet = _stub_packet()

    import app.research.deep as deep_mod
    original = {
        "tech": deep_mod.run_technical,
        "fund": deep_mod.run_fundamental,
        "sent": deep_mod.run_sentiment,
        "cont": deep_mod.run_contrarian,
    }

    def failing(name):
        def _r(p, client=None):
            return AgentResult(
                agent_name=name, lens=None, cost_usd=0.0,
                duration_ms=10, error=f"forced {name} failure",
            )
        return _r

    deep_mod.run_technical = failing("quantitative")
    deep_mod.run_fundamental = failing("fundamental")
    deep_mod.run_sentiment = failing("sentiment_macro")
    deep_mod.run_contrarian = failing("contrarian_risk")
    try:
        import pytest
        with pytest.raises(RuntimeError, match="All four"):
            plan_deep.run(packet)
    finally:
        deep_mod.run_technical = original["tech"]
        deep_mod.run_fundamental = original["fund"]
        deep_mod.run_sentiment = original["sent"]
        deep_mod.run_contrarian = original["cont"]


from app.research.schema import LensView


def test_lens_view_supports_revised_fields():
    """Schema supports optional revised_summary / revised_points / responded_to.

    Phase 2 cross-lens debate adds these as a second-round read; absence
    means no revision happened (legacy plans + toggle-off Deep runs).
    """
    lv = LensView(
        name="quantitative",
        direction="bullish",
        conviction="high",
        summary="initial read",
        points=["p1"],
        revised_summary="after seeing fundamental's bear case I'd downgrade",
        revised_points=["bear case is real but my technicals still hold"],
        responded_to=["fundamental", "contrarian_risk"],
    )
    assert lv.revised_summary == "after seeing fundamental's bear case I'd downgrade"
    assert lv.responded_to == ["fundamental", "contrarian_risk"]


def test_lens_view_revised_fields_default_to_none_or_empty():
    lv = LensView(
        name="quantitative",
        direction="bullish",
        conviction="high",
        summary="round-1 only",
        points=["p1"],
    )
    assert lv.revised_summary is None
    assert lv.revised_points == []
    assert lv.responded_to == []
