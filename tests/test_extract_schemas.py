"""Tests for the LLM extraction schema (pydantic v2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extract.schemas import LLMExtractedCall, NoCallFound


def _good_call(**overrides):
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
    return base


def test_minimal_valid_call():
    call = LLMExtractedCall(**_good_call())
    assert call.ticker == "NVDA"
    assert call.entry_type == "trigger_above"
    assert call.entry_price == 920.0


def test_market_entry_with_no_price_is_valid():
    call = LLMExtractedCall(
        **_good_call(entry_type="market", entry_price=None, entry_evidence=None)
    )
    assert call.entry_price is None


def test_trigger_requires_entry_price():
    with pytest.raises(ValidationError) as exc:
        LLMExtractedCall(**_good_call(entry_price=None, entry_evidence=None))
    assert "requires entry_price" in str(exc.value)


def test_value_without_evidence_rejected():
    with pytest.raises(ValidationError):
        LLMExtractedCall(
            **_good_call(target_price=1000.0, target_evidence=None)
        )


def test_evidence_without_value_rejected():
    with pytest.raises(ValidationError):
        LLMExtractedCall(
            **_good_call(stop_price=None, stop_evidence="stop at 880")
        )


def test_invalid_direction_rejected():
    with pytest.raises(ValidationError):
        LLMExtractedCall(**_good_call(direction="bullish"))


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        LLMExtractedCall(**_good_call(overall_confidence=1.5))
    with pytest.raises(ValidationError):
        LLMExtractedCall(**_good_call(overall_confidence=-0.1))


def test_no_call_found():
    nc = NoCallFound(reason="general macro commentary")
    assert nc.is_trade_call is False
    assert nc.reason == "general macro commentary"
