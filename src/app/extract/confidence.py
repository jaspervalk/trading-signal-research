"""Final confidence + status decision.

`final_confidence` combines:
  - llm.overall_confidence (the model's own assessment)
  - rule.rule_confidence    (validator's assessment)
  - completeness bonus      (more non-null fields → slightly higher confidence)

`decide_status` then maps (final_confidence, presence of failures) → a
`status` string from app.models.
"""

from __future__ import annotations

from app.extract.schemas import LLMExtractedCall
from app.extract.validator import ValidationResult
from app.models import (
    CALL_STATUS_ACCEPTED,
    CALL_STATUS_PENDING_REVIEW,
    CALL_STATUS_REJECTED,
)


def _completeness_bonus(call: LLMExtractedCall) -> float:
    """Reward calls with more concrete fields. Cap at +0.10."""
    n = sum(
        1
        for v in [call.entry_price, call.target_price, call.stop_price]
        if v is not None
    )
    if call.timeframe != "unspecified":
        n += 1
    return min(0.10, 0.025 * n)


def compute_final_confidence(
    call: LLMExtractedCall,
    validation: ValidationResult,
) -> float:
    """Weighted combination of LLM + rule confidence + completeness."""
    # Weighted avg: 60% LLM self-report, 40% rule validator.
    base = 0.6 * call.overall_confidence + 0.4 * validation.rule_confidence
    final = base + _completeness_bonus(call)
    return max(0.0, min(1.0, final))


def decide_status(
    final_confidence: float,
    validation: ValidationResult,
    *,
    accept_threshold: float = 0.7,
    review_threshold: float = 0.5,
) -> str:
    """Map confidence + validator outcome to a status string."""
    if validation.failures:
        return CALL_STATUS_REJECTED
    if final_confidence >= accept_threshold:
        return CALL_STATUS_ACCEPTED
    if final_confidence >= review_threshold:
        return CALL_STATUS_PENDING_REVIEW
    return CALL_STATUS_REJECTED
