"""Tests for the action-label derivation (ADR 0008).

Pure function — given a `DecisionSupportStatus` + setup + entry zone +
optional transcript, returns an `ActionSignal`. We test the full mapping
table plus the historical-only transcript downgrade caveat.
"""

from __future__ import annotations

import pytest

from app.analysis.action import derive_action_signal
from app.analysis.schema import (
    DecisionRubricEntry,
    DecisionSupportStatus,
    EntryZoneCandidate,
    SetupClassification,
    TranscriptContext,
)


def _status(s: str, conf: str = "high", rubric_passes: int = 4, rubric_total: int = 5) -> DecisionSupportStatus:
    rubric = []
    for i in range(rubric_total):
        rubric.append(
            DecisionRubricEntry(
                name=f"r{i}",
                value=1.0,
                threshold=0.0,
                passed=(i < rubric_passes),
                weight="medium",
            )
        )
    return DecisionSupportStatus(
        status=s, confidence=conf, summary=f"stub status {s}", rubric=rubric
    )


def _setup(t: str = "strong_uptrend", conf: str = "high") -> SetupClassification:
    return SetupClassification(setup_type=t, confidence=conf)


def _entry(available: bool = True, rr: float | None = 2.5) -> EntryZoneCandidate:
    return EntryZoneCandidate(available=available, risk_reward_estimate=rr if available else None)


# ---------------------------------------------------------------------------
# Mapping coverage — one test per row of the decision tree.


def test_insufficient_data_to_na():
    a = derive_action_signal(_status("insufficient_data", "low"), _setup(), _entry(False))
    assert a.label == "N/A"
    assert a.confidence == "low"


def test_skip_for_now_to_avoid():
    a = derive_action_signal(_status("skip_for_now", "medium"), _setup("downtrend"), _entry(False))
    assert a.label == "AVOID"
    assert a.confidence == "medium"
    assert "downtrend" in a.derivation


def test_extended_risk_to_reduce():
    a = derive_action_signal(_status("extended_risk", "medium"), _setup("extended_momentum"), _entry(False))
    assert a.label == "REDUCE"
    assert "stretched" in a.derivation or "chase risk" in a.derivation


def test_research_candidate_high_with_zone_to_buy():
    a = derive_action_signal(_status("research_candidate", "high"), _setup(), _entry(True, rr=3.0))
    assert a.label == "BUY"
    assert a.confidence == "high"
    assert "entry_zone available" in a.derivation


def test_research_candidate_high_no_zone_to_accumulate():
    a = derive_action_signal(_status("research_candidate", "high"), _setup(), _entry(False))
    assert a.label == "ACCUMULATE"
    # Conviction degraded since trigger is undefined.
    assert a.confidence == "medium"


def test_research_candidate_medium_to_accumulate():
    a = derive_action_signal(_status("research_candidate", "medium"), _setup(), _entry(True))
    assert a.label == "ACCUMULATE"


def test_research_candidate_low_to_hold():
    a = derive_action_signal(_status("research_candidate", "low"), _setup(), _entry(True))
    assert a.label == "HOLD"
    assert a.confidence == "low"


def test_watch_high_to_hold():
    a = derive_action_signal(_status("watch", "high"), _setup(), _entry(False))
    assert a.label == "HOLD"


def test_watch_medium_to_wait():
    a = derive_action_signal(_status("watch", "medium"), _setup(), _entry(False))
    assert a.label == "WAIT"


def test_wait_for_setup_to_wait():
    a = derive_action_signal(_status("wait_for_setup", "medium"), _setup("range_bound"), _entry(False))
    assert a.label == "WAIT"


# ---------------------------------------------------------------------------
# Auxiliary fields.


def test_pass_rate_computed_from_rubric():
    a = derive_action_signal(
        _status("research_candidate", "high", rubric_passes=3, rubric_total=5),
        _setup(),
        _entry(True),
    )
    assert a.rubric_pass_rate == pytest.approx(0.6)


def test_pass_rate_none_when_no_rubric():
    s = DecisionSupportStatus(status="watch", confidence="medium", summary="x", rubric=[])
    a = derive_action_signal(s, _setup(), _entry(False))
    assert a.rubric_pass_rate is None


def test_historical_only_transcript_attaches_caveat():
    t = TranscriptContext(
        has_data=True,
        n_signals=1,
        n_calls=0,
        n_claims=2,
        coverage_status="historical_only",
        days_since_most_recent=180,
        summary="stub",
    )
    a = derive_action_signal(
        _status("research_candidate", "high"), _setup(), _entry(True), t
    )
    assert a.label == "BUY"  # the caveat doesn't flip the label
    assert any("historical only" in n.lower() for n in a.notes)
    assert any("180" in n for n in a.notes)


def test_fresh_transcript_no_caveat():
    t = TranscriptContext(
        has_data=True,
        n_signals=1,
        n_calls=1,
        n_claims=1,
        coverage_status="fresh",
        days_since_most_recent=2,
        summary="stub",
    )
    a = derive_action_signal(
        _status("research_candidate", "high"), _setup(), _entry(True), t
    )
    assert a.notes == []
