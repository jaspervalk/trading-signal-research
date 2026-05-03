# ADR 0002 — Hybrid call extraction (rules + LLM + validator)

**Status:** accepted
**Date:** 2026-05-03

## Context

Trade calls in YouTube transcripts are buried in a lot of noise. Two extreme strategies fail:

1. **Pure rules / regex.** Misses paraphrased calls ("I think Nvidia is set up nicely above 920"). Fragile to ASR noise. Low recall.
2. **Pure LLM.** Hallucinates fields when the source is vague. Confidently invents stop-losses and targets that the speaker never stated. Cannot be trusted at scale.

We need precision *and* recall, and — more importantly — we need a way to **measure** both.

## Decision

Four-stage hybrid:

1. **Prefilter (rules).** Fast pass over `TranscriptSegment` rows, keeping only segments containing a `$`-prefixed ticker, a 2–5 letter token in our `universe.csv`, or call-language verbs (`watching`, `buying`, `calls on`, `puts on`, `above`, `below`, `breakout`, `breaks`). Drops 90–95% of segments before any LLM tokens are spent.

2. **LLM extractor (Claude, structured tool-use).** Receives the trigger segment plus ±90s of context (configurable). Extracts a strict JSON schema. Two non-negotiable rules in the prompt + schema:
   - Every non-null field carries an `evidence_quote` that is **verbatim** from the source.
   - Missing fields **must** be `null`. The model is forbidden from inferring or "best-guessing".

3. **Validator (rules).** For each extracted call:
   - `ticker` must be in the universe.
   - `evidence_quote` must substring-match the source segment text.
   - If `entry_price` is set, must be within ±50% of the actual market price at `posted_at`. Outside that window → field is rejected (set to null) and flagged.
   - If `direction` is missing but ticker is positively framed (sentiment), default to `long` with low confidence; otherwise `unspecified`.

4. **Confidence scoring.** `final_confidence = f(llm_confidence_per_field, evidence_strength, validator_passes, intra-video_redundancy)`. Calls below `min_final_confidence` (settings.yaml) go to a `pending_review` bucket — surfaced in a notebook for spot-checking, not used in scoring until they pass review or auto-acceptance is shown safe via the gold set.

## Anti-hallucination devices

- **Two-pass extraction.** Pass 1 extracts. Pass 2 receives the extraction + original text and is asked: *"For each non-null field, is it actually supported by the source? Return only the supported fields."* Drops a meaningful tail of confabulated targets/stops.
- **Gold set.** ~200 hand-labeled segments live in `data/gold/extraction_gold.jsonl` (in git). Every extractor change re-runs precision/recall against it. This file is more important than any individual code module.

## Consequences

- LLM cost stays bounded; prefilter does the heavy lifting.
- Field-level validation gives us a clear "false positive" definition for the gold set.
- `evidence_quote` doubles as auditing UI: every call shows the source phrase that justified each field.
- We accept a recall hit on highly-paraphrased vague calls. They land in the "directional only" bucket with low confidence and are evaluated separately.
