# ADR 0006 — Claims and TickerSignals: extraction expansion + aggregation entity

**Status:** accepted
**Date:** 2026-05-05
**Depends on:** [ADR 0005](0005-product-pivot-decision-support.md)
**Extends:** [ADR 0002](0002-extraction-hybrid.md)

## Context

ADR 0002 defined the hybrid extractor for `ExtractedCall` — structured trade calls with ticker, direction, entry/target/stop. The pivot in ADR 0005 makes the *ticker* the primary unit and treats transcripts as an indicator class. Two things follow:

1. Trade calls aren't the only useful signal in a transcript. Catalyst mentions, earnings views, sector calls, macro themes, and risk callouts all carry information about a ticker even when no entry price is stated. The current extractor discards all of this.
2. Per-segment / per-call records aren't the right shape for either dashboard tiles or ML features. The dashboard wants "what did the universe of creators say about NVDA in the last 7 days?" — a pre-aggregated answer, not a raw call list to scan.

## Decision

Add two new persistent entities and extend the LLM tool-use surface.

### 1. `Claim` (new entity, sibling of `ExtractedCall`)

```python
class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int]
    document_id: Mapped[int]                # FK -> documents
    primary_segment_id: Mapped[int | None]  # FK -> transcript_segments

    # The claim itself
    claim_type: Mapped[str]   # see enum below
    ticker: Mapped[str | None]    # may be null for sector/macro claims
    sector: Mapped[str | None]    # populated for sector-level claims
    polarity: Mapped[str]     # 'bullish' | 'bearish' | 'neutral' | 'mixed'
    claim_class: Mapped[str]  # 'factual' | 'opinion' | 'speculation' | 'hype'

    # The claim text the LLM produced + the literal evidence
    summary: Mapped[str]            # one-sentence canonicalization
    evidence_quote: Mapped[str]     # verbatim from source
    context_text: Mapped[str | None]
    context_start_seconds: Mapped[float | None]
    context_end_seconds: Mapped[float | None]

    # Resolution (only for verifiable claim_types like earnings_view)
    resolution_target_at: Mapped[datetime | None]   # e.g., next earnings date
    resolved_at: Mapped[datetime | None]
    resolution_outcome: Mapped[str | None]   # 'correct' | 'wrong' | 'partial' | 'unverifiable'
    resolution_notes: Mapped[str | None]

    # Provenance
    extracted_at: Mapped[datetime]
    extractor_version: Mapped[str]
    llm_confidence: Mapped[float | None]
    final_confidence: Mapped[float]
    status: Mapped[str]   # 'accepted' | 'pending_review' | 'rejected'

    manual_status: Mapped[str]   # mirrors ExtractedCall manual review states
    manual_notes: Mapped[str | None]
```

`claim_type` enum:
- `catalyst` — specific positive/negative event-driven thesis ("AI demand surge for NVDA").
- `risk` — concrete downside thesis ("regulatory risk on TSLA from EU probe").
- `earnings_view` — directional view about an upcoming earnings print. Verifiable on the print date.
- `macro_theme` — broad market regime claim ("rates topping out, growth rotation"). Ticker may be null.
- `sector_view` — directional view on a sector/industry rather than a single name.
- `factual_assertion` — a checkable factual claim ("NVDA's data-center revenue grew 70% YoY"). Verifiable against filings.
- `opinion` — non-falsifiable preference ("I like the chart here").
- `speculation` — directional guess without evidence.
- `hype` — promotional/emotional language without thesis.

`claim_class` is orthogonal to `claim_type`: a `catalyst` can be `factual` (data-center revenue grew) or `speculation` (revenue will grow). The class drives credibility weighting downstream.

### 2. `TickerSignal` (new aggregation entity)

```python
class TickerSignal(Base):
    __tablename__ = "ticker_signals"
    __table_args__ = (
        UniqueConstraint("ticker", "window_end", "window_size", "signal_type"),
        Index("ix_ticker_signal_lookup", "ticker", "window_end"),
    )

    id: Mapped[int]
    ticker: Mapped[str]
    window_end: Mapped[datetime]    # UTC, aligned to trading-day close
    window_size: Mapped[str]        # '1d' | '7d' | '30d'
    signal_type: Mapped[str]        # see below

    # Counts
    n_mentions: Mapped[int]
    n_distinct_creators: Mapped[int]
    n_documents: Mapped[int]

    # Polarity
    net_polarity: Mapped[float]                  # -1..+1, mention-weighted
    credibility_weighted_polarity: Mapped[float] # weighted by creator + claim_class

    # Trade-call specifics (when available)
    avg_entry_distance_pct: Mapped[float | None]   # avg (stated_entry - current_price) / current_price
    avg_target_distance_pct: Mapped[float | None]
    avg_stop_distance_pct: Mapped[float | None]

    # Claim mix
    n_factual: Mapped[int]
    n_opinion: Mapped[int]
    n_speculation: Mapped[int]
    n_hype: Mapped[int]

    # Provenance
    computed_at: Mapped[datetime]
    aggregator_version: Mapped[str]
```

`signal_type` enum:
- `trade_calls` — derived from `ExtractedCall` only.
- `claims_all` — derived from all `Claim` rows.
- `claims_factual` — `Claim`s with `claim_class='factual'` only.
- `creator_consensus` — restricted to creators with high scorecard credibility.

### 3. LLM tool-use extension

The Claude extractor's tool surface grows from 2 tools (`submit_call`, `submit_no_call_found`) to 3:
- `submit_call` — unchanged, feeds `ExtractedCall`.
- `submit_claims` — emits zero-or-more `Claim` records for the same window. Tool-input schema enforces verbatim `evidence_quote` per claim, and explicit `claim_class`.
- `submit_no_signal` — replaces `submit_no_call_found`; signals "no actionable call AND no notable claims."

A single LLM call per window can emit BOTH a call and claims. The validator is extended to apply the same evidence-substring check + ticker universe check to `Claim` rows.

### 4. Gold-set extension

`data/gold/extraction_gold.jsonl` extends to support claims. Each row gains optional `expected_claims: list[ExpectedClaim]`. The eval harness scores per-call AND per-claim precision/recall separately. A claims-specific gold subset (target: 100 hand-labeled segments) is added alongside the existing 200-segment call gold set.

## Anti-hallucination devices (carried forward + extended)

- Verbatim `evidence_quote` requirement applies to every non-null `Claim` field, not just `ticker` and the summary.
- Two-pass validation extends to claims: pass 2 asks "is each claim's evidence character-for-character in the source?" and drops fabricated claims.
- `claim_class` is itself validated: the LLM is forbidden from labeling speculation as factual; a regex/heuristic post-check looks for hedging language ("I think", "could", "might") and downgrades `factual` → `opinion` when the evidence quote contains hedges.

## Aggregation policy (TickerSignal)

- Window alignment: `window_end` is the most recent NYSE trading-day close at-or-before "now."
- Sources: `TickerSignal` aggregates from `ExtractedCall` (status=accepted) and `Claim` (status=accepted). `pending_review` rows are excluded.
- Credibility weighting: claim weight = `creator_scorecard.hit_rate_lower_ci × claim_class_weight`, where class weights are `{factual: 1.0, opinion: 0.5, speculation: 0.25, hype: 0.0}`. A creator without enough resolved calls (`min_n_for_scoring`) gets weight 1.0 (uncalibrated, neutral) until they accumulate.
- Refresh: aggregates recompute nightly during the cron run, after extraction completes. Idempotent on `(ticker, window_end, window_size, signal_type)`.

## Consequences

- The `Claim` table will grow much faster than `ExtractedCall` — every video produces 0 trade calls but typically 5–20 claims of various kinds. Plan accordingly for SQLite size (still manageable; revisit Postgres at ~100M rows).
- The dashboard's ticker page becomes useful with O(1) read against `TickerSignal` instead of O(N) scan of claims/calls.
- The LLM cost per video roughly doubles (more output tokens). Acceptable; prefilter still drops 90%+ of segments before the LLM sees them.
- Claim resolution (`earnings_view`, `factual_assertion`) is its own future work — out of scope for this ADR. The schema reserves the columns; population is deferred until there's a verification pipeline.

## What this ADR is *not* doing

- Not removing `ExtractedCall`. Calls remain a first-class entity; they're a special case of "claim with a stated trigger and target."
- Not committing to a vector index or embeddings layer. `TickerSignal` is structured aggregation; semantic search is a separate decision.
- Not specifying the strategy layer. That's [ADR 0007](0007-strategies-and-walkforward.md).
