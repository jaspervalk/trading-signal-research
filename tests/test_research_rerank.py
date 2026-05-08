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
