"""Tests for the claim validator (per ADR 0006 §"Anti-hallucination devices")."""

from __future__ import annotations

from app.extract.schemas import LLMExtractedClaim
from app.extract.validator import validate_claim
from app.normalize.tickers import Universe


def _u(*tickers: str) -> Universe:
    u = Universe()
    for t in tickers:
        u.by_ticker[t] = {"name": t.title(), "sector": "X"}
    return u


def _claim(**overrides) -> LLMExtractedClaim:
    base = {
        "claim_type": "catalyst",
        "ticker": "NVDA",
        "polarity": "bullish",
        "claim_class": "speculation",
        "summary": "Revenue beat likely on strong AI demand.",
        "evidence_quote": "AI demand looks strong",
        "overall_confidence": 0.7,
    }
    base.update(overrides)
    return LLMExtractedClaim(**base)


def test_accepts_clean_claim():
    src = "I think NVDA will surprise. AI demand looks strong this quarter."
    c = _claim()
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None
    assert not res.failures
    assert res.rule_confidence > 0.7


def test_rejects_when_evidence_not_in_source():
    src = "general market commentary"
    c = _claim(evidence_quote="AI demand looks strong")  # not in src
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is None
    assert any("evidence_not_in_source" in f for f in res.failures)


def test_rejects_ticker_not_in_universe():
    src = "FAKE will rip on the print."
    c = _claim(
        ticker="FAKE",
        evidence_quote="FAKE will rip on the print",
        claim_type="earnings_view",
    )
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is None
    assert any("ticker_not_in_universe" in f for f in res.failures)


def test_accepts_sector_only_claim():
    src = "Semiconductors are catching a bid this week."
    c = _claim(
        claim_type="sector_view",
        ticker=None,
        sector="semiconductors",
        evidence_quote="Semiconductors are catching a bid",
    )
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None
    assert res.accepted_claim.sector == "semiconductors"


def test_downgrades_factual_when_hedged():
    """Per ADR 0006 §6: 'I think', 'might', 'could' disqualify class='factual'."""
    src = "I think NVDA will beat earnings."
    c = _claim(
        claim_class="factual",
        evidence_quote="I think NVDA will beat earnings",
    )
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None
    assert res.accepted_claim.claim_class == "opinion"
    assert res.downgraded_class is True
    assert any("downgraded" in w for w in res.warnings)


def test_keeps_factual_when_unhedged():
    src = "NVDA reported data-center revenue of $30 billion last quarter."
    c = _claim(
        claim_class="factual",
        claim_type="factual_assertion",
        polarity="neutral",
        evidence_quote="reported data-center revenue of $30 billion last quarter",
    )
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None
    assert res.accepted_claim.claim_class == "factual"
    assert res.downgraded_class is False


def test_whitespace_normalization_in_evidence_match():
    src = "AI    demand\nlooks  strong here."
    c = _claim(evidence_quote="AI demand looks strong")
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None


def test_macro_theme_with_null_ticker_passes():
    """A macro claim with no ticker should not be rejected by the universe check."""
    src = "Recession risk is rising into year-end."
    c = _claim(
        claim_type="macro_theme",
        ticker=None,
        polarity="bearish",
        evidence_quote="Recession risk is rising into year-end",
    )
    res = validate_claim(c, source_text=src, universe=_u("NVDA"))
    assert res.accepted_claim is not None
