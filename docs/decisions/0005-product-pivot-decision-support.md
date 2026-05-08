# ADR 0005 — Product pivot: decision-support platform, ticker-first

**Status:** accepted (2026-05-05) · **partially relaxed** by [ADR 0008](0008-action-signal-labeling.md) (2026-05-07)
**Date:** 2026-05-05
**Supersedes parts of:** [0001](0001-source-abstraction.md) framing language; the README's V1 pitch.

> **Update 2026-05-07:** [ADR 0008](0008-action-signal-labeling.md) relaxes the "no naked buy/sell labels" clause in §3 below. An *implied* action label (BUY / ACCUMULATE / HOLD / WAIT / REDUCE / AVOID / N/A) is now emitted as a deterministic view over the existing `DecisionSupportStatus` rubric, with the derivation shown inline. The substrate ("decision support, not advice") is unchanged; the change is purely vocabulary in the UI.

## Context

ADRs 0001–0004 framed this project as a *creator-evaluation* research pipeline: ingest YouTube → extract trade calls → backtest → produce creator scorecards. The headline product was a leaderboard ranking content creators by historical reliability.

Two facts changed the framing:

1. The user (sole operator) intends to use outputs to inform **personal trading decisions**, not just publish a creator leaderboard. The accuracy bar this implies is meaningfully higher than the original portfolio-project framing assumed.
2. The natural unit of decision is **a ticker, not a creator.** A user opening the tool wants to evaluate AAPL — not browse The Trade Risk's scorecard. Creator reliability is one input to that evaluation, not the output.

Treating the product as creator-first is now a misalignment. Treating it as a buy-signal generator is also wrong (single-creator, single-strategy, low-N, no out-of-sample validation). The honest middle is **decision-support**.

## Decision

The product is a **personal decision-support platform for ticker selection.** Outputs are structured rankings, evidence, and confidence levels — never buy/sell recommendations.

Concretely:

1. **Primary unit: the ticker.** The dashboard's headline view is a ticker page (chart, classical technicals, recent extracted claims/calls about that ticker, creator coverage, notes, positions). The creator leaderboard demotes to a secondary view.
2. **Transcripts are an indicator class, not the output.** Aggregated `TickerSignal` features (mention count, net polarity, claim density, credibility-weighted) feed strategies and ML — they are not themselves the alert.
3. **Outputs are decision-support, not advice.** The dashboard always shows: rank + score + which features drove it + sample size + confidence interval + link to source evidence. No naked "buy" / "sell" labels. No probability-of-profit numbers without calibration.
4. **The user pulls the trigger.** No automation will execute orders or unconditionally surface a single "best pick of the day." The model is one input among several the user weighs.

## Consequences

**Architectural:**
- Extraction expands beyond `ExtractedCall` to include `Claim` (catalyst, risk, earnings view, macro theme, opinion vs speculation vs fact). See [ADR 0006](0006-claims-and-ticker-signals.md).
- A new `TickerSignal` aggregation entity becomes the canonical feature shape. Same ADR.
- Backtesting expands from per-call (current) to per-strategy with walk-forward + calibration. See [ADR 0007](0007-strategies-and-walkforward.md).
- The Phase 5 "ranking model" is reframed as **strategy backtests with explainability**. A trained ML ranker remains deferred until claim volume + calibration data exist to support it.

**Operational:**
- Frontend reorients ticker-first. Routes scaffolded under `apps/web/src/app/tickers/` become the primary entry point.
- Creator scorecards stay; their role narrows to "calibrate trust in claims/calls from this creator," feeding into `TickerSignal` credibility weights.

**Hard rules carried forward:**
- Time discipline from [ADR 0003](0003-backtest-assumptions.md) is non-negotiable. The shift to strategy-level backtests does not relax the leakage controls.
- Evidence-quote requirement from [ADR 0002](0002-extraction-hybrid.md) extends to all `Claim` fields, not just `ExtractedCall` fields.

**Disclaimer discipline:**
- Every UI surface that ranks tickers must link to [docs/disclaimer.md](../disclaimer.md) and use language that frames output as "decision support" / "research view," not advice.
- No "buy" / "sell" / "probability of profit" labels in the UI without a calibration plot supporting the number.

## What this ADR is *not* doing

- Not deleting creator scorecards. They become an internal credibility signal.
- Not removing the Discord adapter (Phase 7) or the SourceAdapter ABC.
- Not pivoting the ingestion / extraction / backtest principles. Those stay; new entities slot into the existing flow.
- Not committing to a specific dashboard rewrite. The existing `apps/web` and `apps/api` evolve in place.

## Things explicitly out of scope (still)

The pivot does *not* drag in everything that sounds related. The following remain deferred or rejected:

- **Multilingual transcripts.** No tracked creator needs it. Re-evaluate if a non-English creator is added.
- **Vector DB / embeddings store.** No query currently needs semantic search. Add when claim deduplication or theme clustering justifies it.
- **Cross-video claim deduplication.** Hard NLP problem; aggregate first via `TickerSignal`, dedupe later if signal demands.
- **Postgres migration.** SQLite is right at this volume.
- **External orchestration (Prefect/Dagster).** The launchd cron handles current load.
- **Auto-execution of any kind.** This is decision support; nothing in the system places trades.
