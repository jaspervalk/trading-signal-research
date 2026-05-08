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
