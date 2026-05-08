# ADR 0008 — Implied action signal (BUY / HOLD / SELL labels)

**Status:** accepted
**Date:** 2026-05-07
**Relaxes:** [ADR 0005](0005-product-pivot-decision-support.md) — "no advice language in UI"
**Depends on:** [ADR 0005](0005-product-pivot-decision-support.md), [ADR 0007](0007-strategies-and-walkforward.md)

## Context

ADR 0005 framed the product as decision-support and explicitly forbade buy/sell labels in the UI. The reasoning was that the system did not yet have the evidence base — walk-forward backtests, calibrated probabilities, sample-size-honest rankings — to claim a confidence the framing implied.

Six months later the operator (Jasper, sole user) finds the decision-support vocabulary harder to act on than necessary. Looking at a `research_candidate · high confidence` and translating it back into "should I buy this" every time has friction. Other tools the operator uses speak BUY/HOLD/SELL natively; the cognitive switching cost is real.

At the same time, the underlying machinery has matured: `DecisionSupportStatus` is a real, audited rule-based rubric (see [src/app/analysis/status.py](../../src/app/analysis/status.py)); `EntryZoneCandidate` exists; sample-size caveats are computed; transcript freshness is tracked. The question shifts from "do we have enough to label?" to "is the label a faithful view over what we already compute?"

## Decision

Emit an **implied action label** as a deterministic, transparent view over the existing decision-support state. The label never replaces the underlying status; it is presented next to it, with the derivation visible inline.

### 1. The labels

Seven discrete labels, ordered roughly from "lean long" to "stay out":

| Label        | Operator interpretation                                                |
| ------------ | ---------------------------------------------------------------------- |
| `BUY`        | Constructive setup, high conviction, defined entry trigger.            |
| `ACCUMULATE` | Constructive but trigger missing, or conviction is medium — size down. |
| `HOLD`       | Maintain existing exposure; no fresh entry signal.                     |
| `WAIT`       | No actionable setup yet — keep the ticker on a watchlist.              |
| `REDUCE`     | Trend stretched into chase-risk territory — trim, don't add.           |
| `AVOID`      | Active reasons not to be long now (downtrend, low liquidity, unstable vol). |
| `N/A`        | Not enough data to read.                                               |

`SELL` is intentionally absent. The operator runs long-only swing strategies; a "sell short" label would imply a short-selling workflow the system does not support and the strategies in ADR 0007 do not generate.

### 2. The derivation

Implemented in [src/app/analysis/action.py](../../src/app/analysis/action.py) as a pure function:

```python
def derive_action_signal(
    status: DecisionSupportStatus,
    setup: SetupClassification,
    entry_zone: EntryZoneCandidate,
    transcript: TranscriptContext | None = None,
) -> ActionSignal: ...
```

Decision tree, evaluated in order:

1. `status="insufficient_data"` → **N/A**
2. `status="skip_for_now"` → **AVOID**
3. `status="extended_risk"` → **REDUCE**
4. `status="research_candidate"`:
   - `confidence="high"` + `entry_zone.available` → **BUY**
   - `confidence="high"` + no entry zone → **ACCUMULATE** (downgraded conviction)
   - `confidence="medium"` → **ACCUMULATE**
   - `confidence="low"` → **HOLD**
5. `status="watch"`:
   - `confidence="high"` → **HOLD**
   - else → **WAIT**
6. `status="wait_for_setup"` → **WAIT**
7. fallback → **HOLD**

The function returns an `ActionSignal` carrying:

- `label` — one of the seven above
- `confidence` — `low` / `medium` / `high`
- `derivation` — short, human-readable explanation
- `rubric_pass_rate` — `passed/total` from `DecisionSupportStatus.rubric` (or `None`)
- `notes` — caveats; e.g., a `historical_only` transcript appends a note but does not flip the label

### 3. UI contract

- The label appears in [`ResearchStatusStrip`](../../apps/web/src/components/ResearchStatusStrip.tsx) as the prominent left-side badge, color-keyed to the status palette (BUY = green, REDUCE = amber, AVOID = red, etc.).
- The underlying `DecisionSupportStatus` is shown immediately next to it (`status conf high · rubric 80%`).
- The full `derivation` string is shown directly under the summary in muted-uppercase tape grammar so it never reads as a proclamation.
- `caveats` from the status and `notes` from the action are merged into the bullet list under the strip.

The user must always be able to read why a label was emitted without leaving the view. The label is never the only piece of information shown.

### 4. What this is *not*

- **Not a probability.** No `P(return > 0)` claim. The label is a categorical mapping over rules.
- **Not a strategy.** The walk-forward strategies in ADR 0007 emit `StrategyDecision` with weights and scores; those are independent and unaffected.
- **Not a backtested edge.** When ADR 0007's harness produces validated CAGR / Sharpe / drawdown numbers per strategy, those numbers attach to strategies — not to this label.

## Consequences

**Positive:**

- Reduces cognitive translation friction from "research_candidate (high) + entry zone" → "BUY" for the daily workflow.
- Mapping is auditable: pure function, fully tested, easy to evolve.
- The substrate is unchanged — anyone disagreeing with the label can read the rubric and reach a different conclusion in one screen.
- ADR 0007 walk-forward results, when they land, will provide the missing piece (calibration of the *labels themselves* against realized outcomes).

**Negative / risks:**

- Labels carry social weight that rubrics don't. A user who only reads the badge could lose the epistemic context. Mitigation: the derivation string is shown beneath the summary, in the same visual block, not behind a click.
- The mapping itself is unvalidated against forward returns. Mitigation: when walk-forward harness completes for ≥3 months, run a calibration test by `(label, confidence) → mean forward 5d return` and revisit the thresholds. Track this as the open follow-up below.
- Consumers (notebooks, snapshots, scan rows) may want the label too; for now it is only on `TickerResearchView`. ScanRow and ResearchSnapshot intentionally do not include it; they continue to carry `status` + `status_confidence` as the audit substrate.

**Out of scope:**

- A short-side label (`SELL`, `SHORT`) — gated on the system supporting short-selling strategies.
- A position-sizing recommendation — out of scope for V1; sizing is the operator's call.
- An auto-execution path — explicitly forbidden; no order-routing here, ever.

## Open follow-ups

- After ADR 0007's walk-forward harness has 3+ months of realized outcomes for tickers that received each label, compute calibration: mean / Wilson-CI of forward 5d return per `(label, confidence)` cell. Use the result to refine the decision tree (e.g., demote `ACCUMULATE` to `HOLD` if its mean forward return is no better than `WAIT`).
- Decide whether to surface the label on `ScanRow` / `ResearchSnapshot` once the calibration above stabilizes.

## Disclaimer

The label is research output, not investment advice. The operator evaluates the underlying rubric and pulls the trigger; the system does not execute orders.
