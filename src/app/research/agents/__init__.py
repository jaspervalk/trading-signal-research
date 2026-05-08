"""Multi-agent Deep research mode.

Four parallel Haiku analysts, each with a focused context subset, emit
independent `LensView` outputs. A Sonnet judge then synthesises into the
final `EntryExitPlan` with numeric levels (clamped to deterministic
candidates, same as Quick mode).

See docs/entry-exit-research-plan.md (slice 2) for the architecture and
ADR 0008 for the action-label / decision-support framing the judge stays
within.
"""
