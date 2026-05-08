"""Tests for the watchlist scan rerank feature (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.research.schema import LensView, ScanRerankResult


def test_scan_rerank_result_schema():
    r = ScanRerankResult(
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        rank="high",
        rationale="Strong breakout with no broad-market warning signs.",
        lenses=[
            LensView(name="quantitative", direction="bullish", conviction="high",
                     summary="63d high cleared on volume", points=["p1"]),
            LensView(name="contrarian_risk", direction="neutral", conviction="medium",
                     summary="No crowded-trade signals", points=["p1"]),
        ],
        cost_usd=0.013,
        duration_ms=2400,
    )
    assert r.rank == "high"
    assert len(r.lenses) == 2
    assert r.cost_usd == 0.013


import pytest
from types import SimpleNamespace

from app.research.agents.rerank_judge import (
    RERANK_TOOL_NAME,
    rerank_judge_tool_input_schema,
    run_rerank_judge,
)


def _fake_judge_response(raw_input: dict, input_tokens=600, output_tokens=80):
    block = SimpleNamespace(type="tool_use", name=RERANK_TOOL_NAME, input=raw_input)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model_dump=lambda: {"content": [{"type": "tool_use", "input": raw_input}]},
    )


class _FakeJudgeClient:
    def __init__(self, raw_input: dict):
        self._resp = _fake_judge_response(raw_input)
        class _Messages:
            def __init__(inner, parent): inner.parent = parent
            def create(inner, **kwargs):
                inner.parent.last_kwargs = kwargs
                return inner.parent._resp
        self.messages = _Messages(self)
        self.last_kwargs = {}


def test_rerank_judge_schema_required_fields():
    schema = rerank_judge_tool_input_schema()
    assert "rank" in schema["required"]
    assert "rationale" in schema["required"]
    assert schema["properties"]["rank"]["enum"] == ["high", "medium", "low", "skip"]


def test_rerank_judge_returns_structured_output():
    quant = LensView(name="quantitative", direction="bullish", conviction="high",
                     summary="strong breakout", points=["p1"])
    contra = LensView(name="contrarian_risk", direction="bearish", conviction="medium",
                      summary="crowded long", points=["p1"])
    raw = {
        "rank": "medium",
        "rationale": "Strong tech but Contrarian flags positioning risk; size accordingly.",
    }
    client = _FakeJudgeClient(raw)
    rank, rationale, cost, duration_ms = run_rerank_judge(
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        lenses=[quant, contra],
        last_close=200.0,
        atr_14=4.0,
        client=client,
    )
    assert rank == "medium"
    assert "Contrarian" in rationale or "positioning" in rationale
    assert cost > 0
    assert duration_ms >= 0
    # Verify temperature=0
    assert client.last_kwargs.get("temperature") == 0


# Reuse the minimal ResearchPacket builder from the deep-mode test file. It
# constructs a stub `TickerResearchView` + `CandidateLevels` for AAPL keyed
# at 2026-05-08 — exactly what we need here too.
from tests.test_research_deep import _stub_packet  # noqa: E402

from app.research.rerank import run_rerank  # noqa: E402


def test_run_rerank_happy_path(monkeypatch):
    """Quant + Contrarian both succeed → judge picks 'high'."""
    from app.research import rerank as rerank_mod

    quant_lens = LensView(name="quantitative", direction="bullish", conviction="high",
                          summary="strong setup", points=["p1"])
    contra_lens = LensView(name="contrarian_risk", direction="neutral", conviction="medium",
                           summary="no concerns", points=["p1"])

    def fake_quant(packet, client=None):
        from app.research.agents.base import AgentResult
        return AgentResult(agent_name="quantitative", lens=quant_lens, cost_usd=0.005, duration_ms=1200)

    def fake_contrarian(packet, client=None):
        from app.research.agents.base import AgentResult
        return AgentResult(agent_name="contrarian_risk", lens=contra_lens, cost_usd=0.005, duration_ms=1100)

    def fake_judge(*, ticker, as_of, lenses, last_close, atr_14, client=None):
        return ("high", "Quant: clean setup; Contrarian: no risk flags.", 0.008, 1500)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_quant)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_contrarian)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()

    result = run_rerank(packet)
    assert result.ticker == packet.ticker
    assert result.rank == "high"
    assert "Quant" in result.rationale
    assert len(result.lenses) == 2
    assert result.cost_usd > 0


def test_run_rerank_one_analyst_fails(monkeypatch):
    """If 1 of 2 analysts fails, judge runs with 1 lens. Result still emitted."""
    from app.research import rerank as rerank_mod
    from app.research.agents.base import AgentResult

    contra_lens = LensView(name="contrarian_risk", direction="bearish", conviction="high",
                           summary="overbought + thin float", points=["p1"])

    def fake_quant_fail(packet, client=None):
        return AgentResult(agent_name="quantitative", lens=None, cost_usd=0.0,
                           duration_ms=500, error="Haiku timeout")

    def fake_contrarian(packet, client=None):
        return AgentResult(agent_name="contrarian_risk", lens=contra_lens, cost_usd=0.005, duration_ms=1100)

    def fake_judge(*, ticker, as_of, lenses, last_close, atr_14, client=None):
        # Judge sees only contrarian; picks 'low' or 'skip'
        return ("skip", "Quant failed; Contrarian-only insufficient.", 0.005, 1000)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_quant_fail)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_contrarian)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()
    result = run_rerank(packet)
    assert result.rank == "skip"
    assert len(result.lenses) == 1  # only contrarian survived


def test_run_rerank_both_analysts_fail(monkeypatch):
    """If both analysts fail, return rank='skip' WITHOUT calling the judge."""
    from app.research import rerank as rerank_mod
    from app.research.agents.base import AgentResult

    def fake_fail(packet, client=None):
        return AgentResult(agent_name="x", lens=None, cost_usd=0.0, duration_ms=500, error="fail")

    judge_called = []
    def fake_judge(**kwargs):
        judge_called.append(kwargs)
        return ("high", "shouldn't happen", 0.0, 0)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_fail)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_fail)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()
    result = run_rerank(packet)
    assert result.rank == "skip"
    assert result.error is not None
    assert "both analysts failed" in result.error.lower()
    assert judge_called == []  # judge NOT invoked when both fail


from app.research.scan_rerank import rerank_tickers  # noqa: E402


def test_rerank_tickers_parallel(monkeypatch):
    """Fan out N tickers across a small ThreadPoolExecutor; collect results in
    submission order (not arrival order) for deterministic display."""
    from app.research import scan_rerank as scan_rerank_mod

    def fake_build_packet(ticker, **kwargs):
        # _stub_packet always builds AAPL — patch in the requested ticker so
        # the orchestrator sees the right symbol per call.
        p = _stub_packet()
        p.ticker = ticker
        return p

    def fake_run_rerank(packet, client=None):
        return ScanRerankResult(
            ticker=packet.ticker,
            as_of=packet.as_of,
            rank="medium",
            rationale=f"stub for {packet.ticker}",
            lenses=[],
            cost_usd=0.01,
            duration_ms=100,
        )

    monkeypatch.setattr(scan_rerank_mod, "build_research_packet", fake_build_packet)
    monkeypatch.setattr(scan_rerank_mod, "run_rerank", fake_run_rerank)

    results = rerank_tickers(["AAPL", "NVDA", "TSLA"])
    tickers = [r.ticker for r in results]
    assert tickers == ["AAPL", "NVDA", "TSLA"]  # preserves submission order
    assert all(r.rank == "medium" for r in results)
