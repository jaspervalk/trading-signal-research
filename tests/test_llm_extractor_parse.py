"""Tests for LLMExtractor._parse_response — the multi-tool response handler.

Per ADR 0006, a single Claude response may emit a trade call AND a claims
batch in the same content stream. The parser must collect ALL tool_use
blocks rather than returning on the first match (the pre-pivot behaviour).

These tests inject Anthropic-shape responses directly; no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.extract.llm_extractor import ExtractionResult, LLMExtractor
from app.extract.schemas import (
    CALL_TOOL_NAME,
    CLAIMS_TOOL_NAME,
    NO_CALL_TOOL_NAME,
)


def _tool_use(name: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=data)


def _response(blocks: list, stop_reason: str = "tool_use") -> SimpleNamespace:
    """Build a minimal response shape matching the Anthropic SDK surface."""
    ns = SimpleNamespace(content=blocks, stop_reason=stop_reason)
    # _parse_response calls model_dump() to capture raw_response; supply a stub.
    ns.model_dump = lambda: {"stop_reason": stop_reason, "n_blocks": len(blocks)}
    return ns


def _good_call_input() -> dict:
    return {
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


def _good_claim_input(**overrides) -> dict:
    base = {
        "claim_type": "catalyst",
        "ticker": "NVDA",
        "polarity": "bullish",
        "claim_class": "speculation",
        "summary": "AI demand drives Q3 revenue.",
        "evidence_quote": "AI demand drives Q3 revenue",
        "overall_confidence": 0.7,
    }
    base.update(overrides)
    return base


@pytest.fixture
def extractor() -> LLMExtractor:
    """Build an LLMExtractor without invoking Anthropic auth (we only test parsing)."""
    # Inject a dummy client; we never call .messages.create in these tests.
    return LLMExtractor(client=SimpleNamespace())


def test_parses_call_only(extractor):
    response = _response([_tool_use(CALL_TOOL_NAME, _good_call_input())])
    result = extractor._parse_response(response)

    assert result.is_call
    assert result.call.ticker == "NVDA"
    assert not result.has_claims
    assert result.no_call is None


def test_parses_claims_only(extractor):
    response = _response([
        _tool_use(CLAIMS_TOOL_NAME, {"claims": [_good_claim_input()]}),
    ])
    result = extractor._parse_response(response)

    assert result.call is None
    assert result.has_claims
    assert len(result.claims) == 1
    assert result.claims[0].polarity == "bullish"


def test_parses_call_AND_claims_in_same_response(extractor):
    """The pre-pivot parser would return on the first tool_use block and drop
    the claims; the new parser must keep both."""
    response = _response([
        _tool_use(CALL_TOOL_NAME, _good_call_input()),
        _tool_use(CLAIMS_TOOL_NAME, {"claims": [
            _good_claim_input(),
            _good_claim_input(claim_type="risk", polarity="bearish",
                              claim_class="opinion",
                              summary="Tariff drag.",
                              evidence_quote="tariff drag"),
        ]}),
    ])
    result = extractor._parse_response(response)

    assert result.is_call
    assert result.call.ticker == "NVDA"
    assert len(result.claims) == 2
    assert result.claims[0].polarity == "bullish"
    assert result.claims[1].polarity == "bearish"


def test_parses_no_call_found(extractor):
    response = _response([
        _tool_use(NO_CALL_TOOL_NAME, {"reason": "general macro"}),
    ])
    result = extractor._parse_response(response)

    assert result.no_call is not None
    assert result.no_call.reason == "general macro"
    assert result.is_empty


def test_empty_claims_batch_is_valid(extractor):
    """The LLM saying 'I considered claims and found none' is a real outcome."""
    response = _response([_tool_use(CLAIMS_TOOL_NAME, {"claims": []})])
    result = extractor._parse_response(response)

    assert result.call is None
    assert result.claims == []
    assert result.is_empty


def test_one_bad_claim_does_not_drop_the_batch(extractor):
    """Per the salvage path in _parse_response: malformed individual claims
    should be skipped while valid ones survive."""
    response = _response([
        _tool_use(CLAIMS_TOOL_NAME, {"claims": [
            _good_claim_input(),
            {"claim_type": "catalyst"},  # missing required fields
            _good_claim_input(claim_type="risk", polarity="bearish",
                              claim_class="opinion",
                              summary="Drag.",
                              evidence_quote="drag"),
        ]}),
    ])
    result = extractor._parse_response(response)

    # The first batch.parse will fail (one bad row), salvage path picks up
    # the two valid rows.
    assert len(result.claims) == 2


def test_invalid_call_input_does_not_block_claims(extractor):
    """If the call schema fails to validate, claims from the same response
    should still be retained."""
    bad_call = _good_call_input()
    bad_call["direction"] = "bullish"  # invalid (must be long/short/unspecified)
    response = _response([
        _tool_use(CALL_TOOL_NAME, bad_call),
        _tool_use(CLAIMS_TOOL_NAME, {"claims": [_good_claim_input()]}),
    ])
    result = extractor._parse_response(response)

    assert result.call is None  # call rejected by schema
    assert len(result.claims) == 1
