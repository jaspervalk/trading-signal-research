"""Tests for confidence + status decision."""

from __future__ import annotations

from app.extract.confidence import compute_final_confidence, decide_status
from app.extract.schemas import LLMExtractedCall
from app.extract.validator import ValidationResult
from app.models import (
    CALL_STATUS_ACCEPTED,
    CALL_STATUS_PENDING_REVIEW,
    CALL_STATUS_REJECTED,
)


def _call(**overrides):
    base = {
        "is_trade_call": True,
        "ticker": "NVDA",
        "ticker_evidence": "x",
        "direction": "long",
        "direction_evidence": "x",
        "entry_type": "market",
        "overall_confidence": 0.8,
    }
    base.update(overrides)
    return LLMExtractedCall(**base)


def test_confidence_combines_llm_and_rule():
    call = _call(overall_confidence=0.8)
    val = ValidationResult(accepted_call=call, rule_confidence=0.9)
    final = compute_final_confidence(call, val)
    # 0.6 * 0.8 + 0.4 * 0.9 = 0.84; no completeness bonus on bare market call.
    assert 0.83 < final < 0.85


def test_completeness_bonus_lifts_confidence():
    call = _call(
        entry_type="trigger_above",
        entry_price=100.0,
        entry_evidence="above 100",
        target_price=110.0,
        target_evidence="target 110",
        stop_price=95.0,
        stop_evidence="stop 95",
        timeframe="swing",
        timeframe_evidence="swing trade",
    )
    val = ValidationResult(accepted_call=call, rule_confidence=0.9)
    final_complete = compute_final_confidence(call, val)
    final_bare = compute_final_confidence(_call(), ValidationResult(accepted_call=_call(), rule_confidence=0.9))
    assert final_complete > final_bare


def test_decide_status_thresholds():
    val_ok = ValidationResult(accepted_call=_call())
    assert decide_status(0.9, val_ok) == CALL_STATUS_ACCEPTED
    assert decide_status(0.6, val_ok) == CALL_STATUS_PENDING_REVIEW
    assert decide_status(0.4, val_ok) == CALL_STATUS_REJECTED


def test_decide_status_rejects_on_validator_failures():
    val_failed = ValidationResult(accepted_call=None, failures=["bad"])
    assert decide_status(0.99, val_failed) == CALL_STATUS_REJECTED
