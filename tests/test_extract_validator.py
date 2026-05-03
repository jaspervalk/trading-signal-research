"""Tests for the rule-based validator."""

from __future__ import annotations

from app.extract.schemas import LLMExtractedCall
from app.extract.validator import validate
from app.normalize.tickers import Universe


def _u(*tickers: str) -> Universe:
    u = Universe()
    for t in tickers:
        u.by_ticker[t] = {"name": t.title(), "sector": "X"}
    return u


def _call(**overrides):
    base = {
        "is_trade_call": True,
        "ticker": "NVDA",
        "ticker_evidence": "NVDA looks strong",
        "direction": "long",
        "direction_evidence": "looks strong",
        "entry_type": "trigger_above",
        "entry_price": 920.0,
        "entry_evidence": "above 920",
        "overall_confidence": 0.85,
    }
    base.update(overrides)
    return LLMExtractedCall(**base)


def test_accepts_clean_call():
    src = "I think NVDA looks strong above 920. Target 950, stop 905."
    call = _call(
        target_price=950.0,
        target_evidence="Target 950",
        stop_price=905.0,
        stop_evidence="stop 905",
    )
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is not None
    assert not res.failures
    assert res.rule_confidence > 0.7


def test_rejects_ticker_not_in_universe():
    src = "FAKE looks strong above 920"
    call = _call(ticker="FAKE", ticker_evidence="FAKE looks strong")
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is None
    assert any("ticker_not_in_universe" in f for f in res.failures)


def test_rejects_when_ticker_evidence_not_in_source():
    src = "general market commentary"
    call = _call()  # ticker_evidence "NVDA looks strong" not in src
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is None
    assert any("ticker_evidence_not_in_source" in f for f in res.failures)


def test_nulls_optional_field_when_evidence_missing():
    src = "NVDA looks strong above 920."
    call = _call(
        target_price=950.0,
        target_evidence="target is 950 sure",  # not in source
    )
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is not None
    assert res.accepted_call.target_price is None
    assert res.accepted_call.target_evidence is None
    assert "target_price" in res.nulled_fields


def test_nulls_implausible_price():
    # NVDA's actual price ~920; we say target is 5000 — way off.
    src = "NVDA looks strong above 920. Target 5000."
    call = _call(
        target_price=5000.0,
        target_evidence="Target 5000",
    )
    res = validate(
        call, source_text=src, universe=_u("NVDA"), market_price_at_post=920.0
    )
    assert res.accepted_call is not None
    assert res.accepted_call.target_price is None


def test_downgrades_entry_type_when_entry_price_nulled():
    src = "NVDA looks strong above 920."
    call = _call(
        entry_price=920.0,
        entry_evidence="above NINE HUNDRED twenty",  # not in source
    )
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is not None
    assert res.accepted_call.entry_price is None
    assert res.accepted_call.entry_type == "unspecified"


def test_whitespace_normalization_in_quote_match():
    src = "NVDA  looks   strong\n above 920"
    call = _call(ticker_evidence="NVDA looks strong")
    res = validate(call, source_text=src, universe=_u("NVDA"))
    assert res.accepted_call is not None
