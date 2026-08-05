"""ValuationPanel carries the age of the metadata it was built from."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis import research as research_mod
from app.analysis.schema import ValuationPanel

META = {
    "marketCap": 1.0e12,
    "forwardPE": 30.0,
    "revenueGrowth": 0.25,
    "dividendYield": 0.38,
    "sector": "Technology",
    "industry": "Semiconductors",
}


def test_valuation_panel_has_fetched_at_field():
    assert "fetched_at" in ValuationPanel.model_fields
    assert ValuationPanel().fetched_at is None


def test_build_valuation_stamps_fetched_at(monkeypatch):
    research_mod._resolve_metadata.cache_clear()
    monkeypatch.setattr(research_mod, "_yf_info", lambda t: dict(META))
    research_mod._resolve_metadata("NVDA")

    panel = research_mod._build_valuation(
        ticker="NVDA",
        metadata=dict(META),
        as_of=datetime.now(tz=UTC),
        fetch_metadata=False,
    )
    assert panel.fetched_at is not None
    assert panel.fetched_at.tzinfo is not None
    assert panel.forward_pe == 30.0


def test_empty_metadata_yields_unstamped_panel():
    panel = research_mod._build_valuation(
        ticker="NOSUCH",
        metadata={},
        as_of=datetime.now(tz=UTC),
        fetch_metadata=False,
    )
    assert panel.fetched_at is None
    assert panel.forward_pe is None


def test_metadata_cache_expires(monkeypatch):
    research_mod._resolve_metadata.cache_clear()
    calls = []
    clock = [1000.0]

    def fake_info(ticker):
        calls.append(ticker)
        return dict(META)

    monkeypatch.setattr(research_mod, "_yf_info", fake_info)
    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])

    research_mod._resolve_metadata("NVDA")
    research_mod._resolve_metadata("NVDA")
    assert calls == ["NVDA"]

    clock[0] += research_mod.METADATA_TTL_SECONDS + 1
    research_mod._resolve_metadata("NVDA")
    assert calls == ["NVDA", "NVDA"]


def test_metadata_fetch_failure_returns_empty_dict(monkeypatch):
    research_mod._resolve_metadata.cache_clear()

    def boom(ticker):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(research_mod, "_yf_info", boom)
    assert research_mod._resolve_metadata("NVDA") == {}
