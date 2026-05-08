"""Implied action labels (BUY/ACCUMULATE/HOLD/WAIT/REDUCE/AVOID/N/A).

Per ADR 0008. The label is a derived view over the existing
`DecisionSupportStatus`, `SetupClassification`, `EntryZoneCandidate`, and
`TranscriptContext`. The function is pure: given the same inputs it always
returns the same `ActionSignal`. No model, no probability, no fit.

Mapping (decision tree, evaluated in order):

1. status="insufficient_data"        → N/A
2. status="skip_for_now"              → AVOID
3. status="extended_risk"             → REDUCE
4. status="research_candidate":
     • conf="high"   + entry_zone.available → BUY
     • conf="high"   + no entry_zone        → ACCUMULATE
     • conf="medium"                        → ACCUMULATE
     • conf="low"                           → HOLD
5. status="watch":
     • conf="high"   → HOLD
     • conf in (low, medium) → WAIT
6. status="wait_for_setup"            → WAIT
7. fallback                           → HOLD

Sample-size downgrade: if transcript context is `historical_only` AND the
status is one of (research_candidate, watch), append a caveat. We do *not*
flip the label automatically — sample size is a confidence input upstream
(via `derive_status`), not a second-pass override.
"""

from __future__ import annotations

from app.analysis.schema import (
    ActionSignal,
    DecisionSupportStatus,
    EntryZoneCandidate,
    SetupClassification,
    TranscriptContext,
)


def _pass_rate(status: DecisionSupportStatus) -> float | None:
    if not status.rubric:
        return None
    scored = [r for r in status.rubric if r.passed is not None]
    if not scored:
        return None
    passed = sum(1 for r in scored if r.passed)
    return passed / len(scored)


def derive_action_signal(
    status: DecisionSupportStatus,
    setup: SetupClassification,
    entry_zone: EntryZoneCandidate,
    transcript: TranscriptContext | None = None,
) -> ActionSignal:
    """Map a `DecisionSupportStatus` (+ context) → `ActionSignal`.

    See module docstring for the decision tree.
    """
    pass_rate = _pass_rate(status)
    notes: list[str] = []
    if (
        transcript is not None
        and transcript.coverage_status == "historical_only"
        and status.status in ("research_candidate", "watch")
    ):
        notes.append(
            "Transcript signal is historical only — most recent creator mention is "
            f"{transcript.days_since_most_recent}d old."
        )

    s = status.status
    c = status.confidence

    if s == "insufficient_data":
        return ActionSignal(
            label="N/A",
            confidence="low",
            derivation="status=insufficient_data — not enough history to read.",
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    if s == "skip_for_now":
        return ActionSignal(
            label="AVOID",
            confidence=c,
            derivation=(
                f"status=skip_for_now (setup={setup.setup_type}) — actively bad setup; "
                "skip until conditions change."
            ),
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    if s == "extended_risk":
        return ActionSignal(
            label="REDUCE",
            confidence=c,
            derivation=(
                "status=extended_risk — strong trend but stretched; chase risk is high. "
                "If already long, trim into strength; if flat, wait for a pullback."
            ),
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    if s == "research_candidate":
        if c == "high" and entry_zone.available:
            return ActionSignal(
                label="BUY",
                confidence="high",
                derivation=(
                    f"status=research_candidate (high) + entry_zone available "
                    f"(R/R={entry_zone.risk_reward_estimate}) — clean trigger with "
                    "defined invalidation."
                ),
                rubric_pass_rate=pass_rate,
                notes=notes,
            )
        if c == "high":
            return ActionSignal(
                label="ACCUMULATE",
                confidence="medium",
                derivation=(
                    "status=research_candidate (high) but no entry zone — trend is constructive, "
                    "build position carefully without a defined trigger."
                ),
                rubric_pass_rate=pass_rate,
                notes=notes,
            )
        if c == "medium":
            return ActionSignal(
                label="ACCUMULATE",
                confidence="medium",
                derivation=(
                    "status=research_candidate (medium) — partial conviction; "
                    "size smaller and demand a clean trigger."
                ),
                rubric_pass_rate=pass_rate,
                notes=notes,
            )
        # low conf
        return ActionSignal(
            label="HOLD",
            confidence="low",
            derivation=(
                "status=research_candidate (low) — directionally fine but conviction is "
                "thin; hold existing positions, no new entry."
            ),
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    if s == "watch":
        if c == "high":
            return ActionSignal(
                label="HOLD",
                confidence="medium",
                derivation=(
                    "status=watch (high) — trend intact but trigger not yet formed; "
                    "hold and revisit when the missing components show up."
                ),
                rubric_pass_rate=pass_rate,
                notes=notes,
            )
        return ActionSignal(
            label="WAIT",
            confidence=c,
            derivation=(
                f"status=watch ({c}) — promising but no trigger. Keep on watchlist."
            ),
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    if s == "wait_for_setup":
        return ActionSignal(
            label="WAIT",
            confidence=c,
            derivation=(
                f"status=wait_for_setup (setup={setup.setup_type}) — no actionable setup right now."
            ),
            rubric_pass_rate=pass_rate,
            notes=notes,
        )

    return ActionSignal(
        label="HOLD",
        confidence="low",
        derivation=f"Unmapped status={s} — defaulting to HOLD.",
        rubric_pass_rate=pass_rate,
        notes=notes,
    )


__all__ = ["derive_action_signal"]
