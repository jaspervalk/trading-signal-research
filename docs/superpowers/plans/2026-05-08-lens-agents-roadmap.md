# Lens-Agents Roadmap

> **Status (2026-05-08):** roadmap doc. Each phase has its own plan file (or will, before execution).

## Why this exists

The Deep-mode entry/exit research panel (4 Haiku analysts → Sonnet judge) shipped 2026-05-08 ([recent-changes §6](../recent-changes-2026-05-08.md), [§12](../recent-changes-2026-05-08.md) for the variance fix). After variance was eliminated, the obvious next question is: **are we using these agents as well as we can?**

Architectural critique answered: **no.** Three classes of underuse:

1. **No accuracy feedback loop.** Each lens votes equally with the others regardless of historical track record. The `CreatorScorecard` infrastructure (Wilson CIs, regime split) has the answer — apply the same intellectual move to lenses.
2. **No cross-examination.** Analysts emit independent reads; the judge synthesises. The bear case never directly responds to the bull case. Adding a Round 2 (each lens sees the others' summaries) would catch crossings.
3. **Reuse opportunities.** The 4-lens panel is currently mounted on a single product surface (entry/exit research per ticker). The same system applied to scan reranking, creator-scoring, walk-forward interpretation, and daily briefing extracts more value per dollar of LLM spend.

## Phases

Phases are **independent and shippable**. Each phase = its own feature branch + PR. Earlier phases unblock later ones (notably Phase 1 → Phase 6) but you can pause at any phase boundary.

| # | Name | What | Plan | Status |
|---|---|---|---|---|
| 1 | **Lens accuracy infrastructure** | Persist every lens call + outcome at 1d/3d/5d/21d. CLI + API for `LensScorecard` (Wilson CI, regime split). Recording is silent — does NOT change runtime decisions. | [2026-05-08-lens-phase-1-accuracy-infra.md](2026-05-08-lens-phase-1-accuracy-infra.md) | **Ready** |
| 2 | **Cross-lens debate round** | After Round 1 independent reads, each lens sees the others' summaries and emits an optional revised read. Schema gains `revised_*` fields. Cost: +~$0.02/Deep run. | TBD before exec | Sketched below |
| 3 | **Watchlist scan reranking** | Apply 4-lens panel (or reduced 2-lens) to N watchlist tickers in parallel; cached daily; reranks the existing `tsr scan` output. Cost: ~$0.40-0.80/day. | TBD | Sketched below |
| 4 | **Adversarial pairing** | Bull-X / Bear-X analyst pairs (Quant, Fundamental, Sentiment) so debate is structural, not just Contrarian-vs-the-rest. 4 → 7 analysts; judge synthesises pairs. | TBD | Sketched below |
| 5 | **Style-aware judges** | Three Sonnet judge variants (momentum / trend-pullback / contrarian) routed by `setup.setup_type`. Meta-judge surfaces all three or picks. | TBD | Sketched below |
| 6 | **Lens-weighted judge synthesis** | Gated on Phase 1 data (≥3 months). Pass `LensScorecard` summaries into the judge prompt: "Quant has been 7/10 on breakouts; Fundamental 4/10 in chop." Judge weights accordingly. | TBD | Sketched below |
| 7 | **Creator-scoring lens panel** | Apply 4-lens read to a creator's recent calls quarterly. Augments mechanical `CreatorScorecard` with qualitative reads ("Quant: Stockbee's pattern recognition is sound; Contrarian: cherry-picks winners"). | TBD | Sketched below |
| 8 | **Walk-forward result interpretation** | Apply lens panel to ADR 0007 strategy backtest results. "Quant: this strategy works in trends, breaks in chop. Contrarian: probably overfit to 2024 momentum." | TBD | Sketched below |
| 9 | **Daily cron briefing** | Sentiment + Contrarian lenses on the day's new claims → "what changed overnight" Slack/email digest. Optional. | TBD | Sketched below |

## Execution discipline

Each phase:
1. Writing-plans skill produces a fully-fleshed task list before code is written.
2. Subagent-driven-development executes task-by-task with two-stage review.
3. Branch + PR per phase. No phases bundled.
4. `pytest -q` green at every commit.
5. Phase 1 must accumulate ≥30 days of `LensOutcome` rows before Phase 6 can ship — the prompt-injection of scorecards needs real data, not noise.

## Phase sketches (not yet plan files)

These get fleshed into `docs/superpowers/plans/<date>-lens-phase-N-*.md` files immediately before execution. Order is recommended but not strict.

### Phase 2 — Cross-lens debate

- Schema: `LensView` adds `revised_summary: str | None`, `revised_points: list[str]`, `responded_to: list[str]` (lens names this lens engaged with).
- `deep.py` flow: `run_agents_parallel` for round 1 → assemble lens-block → second `run_agents_parallel` where each agent receives the *other three* round-1 summaries and may revise. Judge sees revised versions if present.
- Toggle behind `configs/settings.yaml` flag `research.deep.cross_lens_round: false` — start opt-in.
- Tests: revised view persists across cache; if a lens fails round 2, fall back to round 1; total-failure semantics unchanged.

### Phase 3 — Watchlist scan reranking

- New endpoint: `POST /research/scan-rerank?source=watchlist` (or `?tickers=AAPL,NVDA,...`).
- Iterates tickers; for each, runs a *reduced* lens panel (Quantitative + Contrarian-Risk only — fastest dimensions for ranking) → judge picks a "research-priority" rank in {high, medium, low, skip}.
- Cached at `(ticker, day)` granularity; reuse `ResearchPlan.day_key` pattern.
- CLI: `tsr scan --rerank` (composes existing `tsr scan` + the new endpoint).
- Cost cap: `research.monthly_budget_usd` from `settings.yaml` (already noted as "not enforced yet" in [recent-changes §8](../recent-changes-2026-05-08.md)).

### Phase 4 — Adversarial pairing

- New analysts: `bull_quantitative`, `bear_quantitative`, `bull_fundamental`, `bear_fundamental`, `bull_sentiment_macro`, `bear_sentiment_macro`. Contrarian-Risk stays as the single overall risk read.
- 4 → 7 analysts. Total cost ~$0.06-0.10 per Deep run (was ~$0.04-0.10).
- Judge schema: lens panel becomes `pairs: [{name, bull_view, bear_view}, contrarian]`.
- Frontend: `LensPanel` becomes a 3-column debate view (`Quant Bull | Quant Bear | →`) + Contrarian.

### Phase 5 — Style-aware judges

- `judge.py` gains `JUDGE_VARIANTS = {"momentum": SYSTEM_PROMPT_MOMENTUM, "trend_pullback": SYSTEM_PROMPT_PULLBACK, "contrarian": SYSTEM_PROMPT_CONTRARIAN}`.
- Routing: `setup.setup_type` selects a judge variant; on ambiguous setups, run all three and surface side-by-side ("research mode").
- Independent of Phase 4.

### Phase 6 — Lens-weighted judge synthesis

- Gated on ≥30 days of Phase 1 outcomes per lens.
- `_format_lenses_block` in `judge.py` gets a new sibling `_format_lens_scorecard_summary(scorecards)` that emits "Quantitative: 12/18 hit rate at 5d (66%, 95% CI 41-86), strongest in trends." into the judge's user prompt.
- The judge is told: "Weight lens convictions by their historical accuracy in the current regime."
- No hardcoded weights — the model picks.

### Phase 7 — Creator-scoring lens panel

- Cron quarterly per creator: `tsr score-creators --lens-panel`.
- Each creator gets a 4-lens read of their last quarter's accepted calls + claims:
  - Quant: pattern quality
  - Fundamental: thesis durability
  - Sentiment: hype level / overconfidence
  - Contrarian: cherry-picking, survivorship bias, hidden losers
- Augments `CreatorScorecard` (mechanical) with `CreatorLensPanel` (qualitative).
- One Deep run per creator per quarter ≈ ~$0.04 each. Cheap.

### Phase 8 — Walk-forward result interpretation

- After every `tsr backtest-strategy --persist`: optional `--with-lens-interp` flag runs the panel on the result.
- Lenses see: strategy name, regime breakdown, Sharpe, max DD, hit rate Wilson CIs, calibration plot data.
- Output written to `WalkForwardResultRow.lens_interpretation` JSON column (new field).
- Useful for the analyst-grade case-study writeups CLAUDE.md mentions.

### Phase 9 — Daily cron briefing

- Add to `scripts/daily_run.sh`: `tsr daily-brief`.
- Command runs Sentiment + Contrarian on the day's accepted claims (across all creators).
- Output: short markdown ("Sentiment: macro tone shifting hawkish; 4 creators flagged BTC weakness. Contrarian: 3 IPOs being heavily promoted — historic loss pattern.") delivered via existing logging or optional Slack webhook.

## Telemetry / health signals

Across all phases, three things stay observable:

1. **Cost per Deep run**, recorded on every `EntryExitPlan.cost_usd`. Already tracked.
2. **Lens-coverage rate** — fraction of Deep runs where all 4 lenses returned non-null. New: `LensSnapshot.created_at` makes this trivially queryable.
3. **Lens accuracy by direction × regime** — Phase 1 ships this in `LensScorecard`.

## Out of scope for the entire roadmap

- Multi-timeframe analyst splits (Quant-2D vs Quant-2W). Overkill for swing-trading product.
- TradingAgents framework integration. Already deferred per [CLAUDE.md](../../CLAUDE.md) and §8 of recent-changes (calibration debt + ADR 0005 framing friction).
- ML-driven lens weighting. Requires order-of-magnitude more data than Phase 6 needs.
- Real-time tick-level lens reads. The system's clock is `posted_at` / `as_of`, not live tick.

## Cross-references

- [docs/decisions/0005-product-pivot-decision-support.md](../decisions/0005-product-pivot-decision-support.md) — load-bearing ADR; nothing in this roadmap should violate decision-support framing.
- [docs/decisions/0007-strategies-and-walkforward.md](../decisions/0007-strategies-and-walkforward.md) — outcome-window contract that Phase 1 piggybacks for lens-outcome computation.
- [docs/entry-exit-research-plan.md](../entry-exit-research-plan.md) — original multi-lens design memo.
- [docs/recent-changes-2026-05-08.md](../recent-changes-2026-05-08.md) — §6 ships the lens panel; §12 fixes its variance.
