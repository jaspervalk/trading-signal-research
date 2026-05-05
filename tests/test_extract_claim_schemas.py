"""Tests for the LLMExtractedClaim schema (per ADR 0006)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extract.schemas import (
    CLAIMS_TOOL_NAME,
    LLMClaimsBatch,
    LLMExtractedClaim,
    claims_tool_input_schema,
)


def _good_claim(**overrides):
    base = {
        "claim_type": "catalyst",
        "ticker": "NVDA",
        "polarity": "bullish",
        "claim_class": "speculation",
        "summary": "AI demand will keep driving data-center revenue.",
        "evidence_quote": "AI demand will keep driving data-center revenue",
        "overall_confidence": 0.7,
    }
    base.update(overrides)
    return base


def test_minimal_valid_claim():
    c = LLMExtractedClaim(**_good_claim())
    assert c.claim_type == "catalyst"
    assert c.ticker == "NVDA"
    assert c.polarity == "bullish"


def test_macro_theme_allows_null_ticker_and_sector():
    c = LLMExtractedClaim(
        **_good_claim(
            claim_type="macro_theme",
            ticker=None,
            sector=None,
            polarity="bearish",
            summary="Rates staying high pressures growth multiples.",
            evidence_quote="rates staying high pressures growth multiples",
        )
    )
    assert c.claim_type == "macro_theme"
    assert c.ticker is None
    assert c.sector is None


def test_earnings_view_requires_ticker():
    with pytest.raises(ValidationError) as exc:
        LLMExtractedClaim(
            **_good_claim(claim_type="earnings_view", ticker=None)
        )
    assert "earnings_view" in str(exc.value).lower()


def test_catalyst_requires_ticker_or_sector():
    with pytest.raises(ValidationError):
        LLMExtractedClaim(
            **_good_claim(claim_type="catalyst", ticker=None, sector=None)
        )


def test_sector_view_accepts_sector_only():
    c = LLMExtractedClaim(
        **_good_claim(
            claim_type="sector_view",
            ticker=None,
            sector="semiconductors",
            polarity="bullish",
        )
    )
    assert c.sector == "semiconductors"


def test_invalid_polarity_rejected():
    with pytest.raises(ValidationError):
        LLMExtractedClaim(**_good_claim(polarity="positive"))


def test_invalid_claim_class_rejected():
    with pytest.raises(ValidationError):
        LLMExtractedClaim(**_good_claim(claim_class="bias"))


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        LLMExtractedClaim(**_good_claim(overall_confidence=1.5))
    with pytest.raises(ValidationError):
        LLMExtractedClaim(**_good_claim(overall_confidence=-0.1))


def test_claims_batch_empty_list_is_valid():
    """An empty batch is the LLM saying 'I considered claims and found none'."""
    batch = LLMClaimsBatch(claims=[])
    assert batch.claims == []


def test_claims_batch_multiple():
    batch = LLMClaimsBatch(
        claims=[
            LLMExtractedClaim(**_good_claim()),
            LLMExtractedClaim(
                **_good_claim(
                    claim_type="risk",
                    polarity="bearish",
                    claim_class="opinion",
                    summary="Tariff overhang weighs on the multiple.",
                    evidence_quote="tariff overhang weighs on the multiple",
                )
            ),
        ]
    )
    assert len(batch.claims) == 2
    assert batch.claims[0].polarity == "bullish"
    assert batch.claims[1].polarity == "bearish"


def test_claims_tool_schema_is_object():
    schema = claims_tool_input_schema()
    assert schema["type"] == "object"
    assert "claims" in schema["properties"]
    # The wrapping is the whole point — Anthropic tool input must be an object.


def test_claims_tool_name():
    assert CLAIMS_TOOL_NAME == "submit_claims"
