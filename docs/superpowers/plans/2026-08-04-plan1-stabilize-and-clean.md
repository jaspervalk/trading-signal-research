# Plan 1: Stabilize & Clean Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 36-path dirty working tree into reviewable commits, delete dead weight, stand up real CI, and land ADR 0009 — producing a clean base for the Analysis/Portfolio restructure.

**Architecture:** No new features. Four uncommitted work threads (Quick-mode bug fixes, fundamentals enrichment, sentiment web-search, screener) become four coherent commits; junk and dead code are removed with grep-guards + test runs between every commit; the scaffold Playwright CI is replaced by a pytest workflow; the restructure decision is recorded as ADR 0009.

**Tech Stack:** git, pytest (`.venv/bin/python -m pytest` — bare `python` is NOT on PATH), Alembic, GitHub Actions, uv.

## Global Constraints

- Never commit `data/`, `CLAUDE.md` (gitignored by design), or `.env`.
- Run the focused tests named in each task BEFORE its commit; run the FULL suite in Task 1 and Task 11. Baseline: **471 tests collected, expected all passing**.
- Every commit message ends with the trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Work on `main` directly (solo repo, no remote CI gate yet); do NOT push until Task 11 passes.
- If any test fails at baseline (Task 1), STOP and report — do not commit on a red suite.
- All paths relative to repo root `/Users/jaspervalk/Documents/projects/trading-signal-research`.

---

### Task 1: Baseline verification

**Files:** none modified.

**Interfaces:**
- Consumes: current dirty working tree.
- Produces: a recorded green baseline (test count + pass/fail) that later tasks compare against.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -5`
Expected: `471 passed` (number may drift slightly; record the actual count). If anything fails, STOP — report the failures; do not proceed to commits.

- [ ] **Step 2: Snapshot the dirty state for reference**

Run: `git status --porcelain > /tmp/plan1-dirty-baseline.txt && wc -l /tmp/plan1-dirty-baseline.txt`
Expected: 36 lines.

---

### Task 2: Commit Thread A — Quick/Deep robustness bug fixes

**Files:**
- Commit (already modified, no edits needed): `src/app/research/quick.py`, `src/app/research/exits.py`, `src/app/research/schema.py`, `src/app/research/agents/judge.py`, `tests/test_research_quick.py`, `tests/test_research_exits.py`, `tests/test_research_deep.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: first clean commit; later threads (B, C) touch disjoint files so ordering is safe.

- [ ] **Step 1: Verify the thread's tests pass**

Run: `.venv/bin/python -m pytest tests/test_research_quick.py tests/test_research_exits.py tests/test_research_deep.py -q 2>&1 | tail -3`
Expected: all pass (~120+ tests across the three files).

- [ ] **Step 2: Confirm no thread-B/C files sneak in**

Run: `git diff --name-only src/app/research/quick.py src/app/research/exits.py src/app/research/schema.py src/app/research/agents/judge.py`
Expected: exactly those 4 paths listed (each has a diff).

- [ ] **Step 3: Commit**

```bash
git add src/app/research/quick.py src/app/research/exits.py src/app/research/schema.py \
        src/app/research/agents/judge.py tests/test_research_quick.py \
        tests/test_research_exits.py tests/test_research_deep.py
git commit -m "fix(research): harden Quick/Deep against truncation, bare-string lists, degenerate bands

- raise Quick max_tokens 4000->16000, log stop_reason==max_tokens, tighten
  schema maxItems; add _recover_misformatted_response repair layer
- _wrap_bare_string validator on LensView/EntryExitPlan list fields
  (HIMS incident: 1746-char entries in bull_case)
- drop degenerate entry bands (>5x or <0.05x ATR); filter invalidation
  candidates below lowest entry low; _resolve_invalidation post-LLM snap,
  shared by Quick and Deep judge

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Commit Thread B — Fundamental lens enrichment

**Files:**
- Commit: `src/app/market/fundamentals.py` (new), `src/app/market/peer_comparison.py` (new), `src/app/research/context.py`, `src/app/research/agents/fundamental.py`, `apps/api/app/routes/research.py`, `tests/test_market_fundamentals.py` (new), `tests/test_market_peer_comparison.py` (new), `tests/test_research_fundamental_enriched.py` (new)

**Interfaces:**
- Consumes: Task 2 committed (schema.py changes it depends on are in history).
- Produces: `market.fundamentals.fetch_fundamentals_extended(ticker) -> FundamentalsExtended` and `market.peer_comparison.fetch_peer_comparison(ticker, sector, industry) -> PeerComparison`, gated by `ResearchPacket` flag `with_deep_extras` — Plan 2 will fold these into the unified marketdata client.

- [ ] **Step 1: Verify the thread's tests pass**

Run: `.venv/bin/python -m pytest tests/test_market_fundamentals.py tests/test_market_peer_comparison.py tests/test_research_fundamental_enriched.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 2: Commit**

```bash
git add src/app/market/fundamentals.py src/app/market/peer_comparison.py \
        src/app/research/context.py src/app/research/agents/fundamental.py \
        apps/api/app/routes/research.py tests/test_market_fundamentals.py \
        tests/test_market_peer_comparison.py tests/test_research_fundamental_enriched.py
git commit -m "feat(research): statements-derived fundamentals + peer comparison for the Fundamental lens

- FundamentalsExtended: 3y revenue/margin/net-income trajectories, FCF,
  balance-sheet strength, capital allocation (yf financials/cashflow/balance_sheet)
- PeerComparison: curated (sector, industry)->peers map + sector fallback,
  median-relative reads
- ResearchPacket.with_deep_extras gates the +1-3s fetches; deep runs only
- Fundamental agent prompt rewritten into 6 sectioned blocks, max_tokens 2000

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Commit Thread C — Sentiment-Macro web search

**Files:**
- Commit: `src/app/research/agents/base.py`, `src/app/research/agents/sentiment.py`, `src/app/config.py`, `tests/test_research_sentiment_macro_websearch.py` (new)

**Interfaces:**
- Consumes: Tasks 2–3 committed.
- Produces: `run_agent_with_web_search()` in `agents/base.py`; settings `research.deep.sentiment_web_search: bool = True`, `sentiment_web_search_max_uses: int = 1` (config.py:64-65) — Task 6 surfaces them in settings.yaml.

- [ ] **Step 1: Verify the thread's tests pass**

Run: `.venv/bin/python -m pytest tests/test_research_sentiment_macro_websearch.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 2: Commit**

```bash
git add src/app/research/agents/base.py src/app/research/agents/sentiment.py \
        src/app/config.py tests/test_research_sentiment_macro_websearch.py
git commit -m "feat(research): Sentiment-Macro lens gains web search; creator claims demoted to one input

- run_agent_with_web_search: web_search_20250305 server tool, tool_choice=auto,
  usage priced at \$0.01/search, citations extracted
- sentiment lens re-scoped to sector/regulatory/geopolitical/macro; explicit
  'no coverage is NOT bearish' rule; no-search fallback template
- config: sentiment_web_search (default on), sentiment_web_search_max_uses=1

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Commit Thread D — Screener (with skill-doc fix first)

**Files:**
- Modify: `.claude/skills/screen/SKILL.md` (lines 9 and 26), `docs/AGENTS_AND_SKILLS.md` (line ~70)
- Commit: `src/app/screener/` (all, new), `src/app/cli.py`, `configs/screen_universe.csv` (new), `configs/universe.csv`, `.claude/skills/screen/` (new), `docs/AGENTS_AND_SKILLS.md`, `tests/test_screener_universe.py`, `tests/test_screener_filters.py`, `tests/test_screener_cache.py`, `tests/test_screener_pipeline.py` (all new)

**Interfaces:**
- Consumes: nothing.
- Produces: `tsr screen` + `tsr screen-refresh-universe` commands; `screener.pipeline.run_screen()`. Plan 2 will re-point `screener/fetcher.py` at `analysis/indicators` and the unified data client.

- [ ] **Step 1: Fix the nonexistent-command references**

`tsr research deep <TICKER>` does not exist (`tsr research` has no `deep` subcommand; Deep mode is `POST /research/deep/{ticker}` only). In `.claude/skills/screen/SKILL.md`:

Line 9 — replace:
```
Run the stock screener and present a short summary of candidates worth handing to `tsr research deep <TICKER>`.
```
with:
```
Run the stock screener and present a short summary of candidates worth researching next via `tsr research <TICKER>` (deterministic view) or Deep mode in the dashboard (`POST /research/deep/{ticker}`).
```

Line 26 — replace:
```
   - Suggested next steps — typically: hand the top 3-5 to `tsr research deep <TICKER>`.
```
with:
```
   - Suggested next steps — typically: run `tsr research <TICKER>` on the top 3-5, then Deep mode from the dashboard for the strongest 1-2.
```

- [ ] **Step 2: Apply the same fix in docs/AGENTS_AND_SKILLS.md**

Run: `grep -n "research deep" docs/AGENTS_AND_SKILLS.md`
Expected: one hit near line 70. Edit that line with the same replacement rule (`tsr research deep <TICKER>` → `tsr research <TICKER>`, Deep via dashboard/API). Re-run the grep; expected: no hits.

- [ ] **Step 3: Verify screener tests pass**

Run: `.venv/bin/python -m pytest tests/test_screener_universe.py tests/test_screener_filters.py tests/test_screener_cache.py tests/test_screener_pipeline.py -q 2>&1 | tail -3`
Expected: all pass (~119 tests in the 8 new files overall; these 4 are the screener's).

- [ ] **Step 4: Commit**

```bash
git add src/app/screener/ src/app/cli.py configs/screen_universe.csv configs/universe.csv \
        .claude/skills/screen/ docs/AGENTS_AND_SKILLS.md \
        tests/test_screener_universe.py tests/test_screener_filters.py \
        tests/test_screener_cache.py tests/test_screener_pipeline.py
git commit -m "feat(screener): filter-funnel screener over seed universe + CLI + /screen skill

- funnel: universe -> rate-limited fetch (24h disk cache) -> 4 independent
  filters (value/growth/quality/technical) -> additive-only creator coverage
- no composite score; pass = any enabled filter; coverage never in sort key
- tsr screen + tsr screen-refresh-universe (Wikipedia S&P500+NDX)
- /screen skill; fixed references to nonexistent 'tsr research deep'
- universe.csv: add NOW, HLIT, HIMS, IREN, NBIS, BE

Known debts (Plan 2): duplicates analysis/indicators math; own yfinance
path+cache; config-free thresholds; universe.csv vs screen_universe.csv
BRK-B/BRK.B mismatch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Config defaults — debate on, sentiment settings surfaced

**Files:**
- Modify: `configs/settings.yaml` (lines 61–67)

**Interfaces:**
- Consumes: Task 4 (config.py already defines the sentiment keys with matching defaults).
- Produces: settings.yaml as the single visible source of Deep-mode defaults.

- [ ] **Step 1: Update the research block**

Replace lines 61–67 of `configs/settings.yaml`:
```yaml
# Entry/exit research feature (Quick + Deep modes).
research:
  deep:
    # Phase 2: round-2 cross-lens debate after the 4 analysts post round-1.
    # Default off so existing Deep runs behave identically. Flip to true
    # to enable revisions (cost +~$0.02/run, +~10s).
    cross_lens_round: true
```
with:
```yaml
# Entry/exit research feature (Quick + Deep modes).
research:
  deep:
    # Round-2 cross-lens debate after the 4 analysts post round-1.
    # ON by default since 2026-08 (cost +~$0.02/run, +~10s per Deep run).
    cross_lens_round: true
    # Sentiment-Macro lens may issue web searches (+~$0.01/search).
    sentiment_web_search: true
    sentiment_web_search_max_uses: 1
```

- [ ] **Step 2: Verify config still loads**

Run: `.venv/bin/python -c "from app.config import load_settings; s = load_settings(); print(s.research.deep.cross_lens_round, s.research.deep.sentiment_web_search)"`
Expected: `True True`. (If `load_settings` is not the loader's name, check `src/app/config.py` for the module-level loader used by `cli.py` and call that; assert the same two values.)

- [ ] **Step 3: Commit**

```bash
git add configs/settings.yaml
git commit -m "chore(config): enable cross-lens debate by default; surface sentiment web-search settings

Deliberate cost/behaviour decision: every Deep run now includes the debate
round (+~\$0.02, +~10s) and up to 1 web search (+~\$0.01).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Commit docs + vendored tooling; drop the duplicate skill root

**Files:**
- Commit: `docs/discord-adapter-plan.md` (new), `.claude/skills/find-skills` (new), `skills-lock.json` (new)
- Delete: `.agents/` (duplicate of `.claude/skills/find-skills`)

**Interfaces:**
- Consumes: decision (c) — Discord stays on the roadmap.
- Produces: single skill root at `.claude/skills/`.

- [ ] **Step 1: Confirm `.agents/` is a duplicate before deleting**

Run: `diff -r .agents/skills/find-skills .claude/skills/find-skills && echo IDENTICAL`
Expected: `IDENTICAL` (or only trivial whitespace). If they differ materially, keep the `.claude` copy and note the delta in the commit body.

- [ ] **Step 2: Delete the duplicate**

Run: `rm -rf .agents/`

- [ ] **Step 3: Commit**

```bash
git add docs/discord-adapter-plan.md .claude/skills/find-skills skills-lock.json
git commit -m "docs: Discord adapter scoping memo; chore: vendor find-skills skill under .claude only

Discord stays on the roadmap as a side-feature source (decision 2026-08-04);
the open question is extracting/locating useful signal, not the adapter.
Removed duplicate .agents/ skill root.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Delete junk (scratch dirs, screenshots, root Playwright scaffold)

**Files:**
- Delete: `.playwright-mcp/`, `docs/screenshots/`, `data/tsr.sqlite.bak-pre-alembic`, `e2e/`, `playwright.config.ts`, root `package.json`, root `package-lock.json`, root `node_modules/`, `.github/workflows/playwright.yml`
- Modify: `.gitignore` (add `.playwright-mcp/`)

**Interfaces:**
- Consumes: nothing.
- Produces: no root-level Node toolchain; Task 9 replaces the deleted CI workflow.

- [ ] **Step 1: Check which targets git tracks**

Run: `git ls-files e2e playwright.config.ts package.json package-lock.json .github/workflows/playwright.yml docs/screenshots`
Expected: the Playwright scaffold files and workflow are tracked; `.playwright-mcp/` and `docs/screenshots/` are untracked. Use `git rm -r` for tracked, `rm -rf` for untracked.

- [ ] **Step 2: Guard — `_design/` may contain wanted design assets**

Run: `git ls-files _design | head` and `ls _design 2>/dev/null | head`
Only delete `_design/_*.png`, `_design/_*.mjs`, `_design/_smoke_walkforward.py` if untracked scratch; leave anything tracked alone (out of scope for this plan).

- [ ] **Step 3: Delete**

```bash
git rm -r --quiet e2e playwright.config.ts .github/workflows/playwright.yml
git rm --quiet package.json package-lock.json 2>/dev/null || rm -f package.json package-lock.json
rm -rf node_modules .playwright-mcp docs/screenshots data/tsr.sqlite.bak-pre-alembic
rm -f _design/_*.png _design/_*.mjs _design/_smoke_walkforward.py
```

- [ ] **Step 4: Gitignore the agent scratch dir**

Append to `.gitignore`:
```
.playwright-mcp/
```

- [ ] **Step 5: Verify the web app is untouched**

Run: `ls apps/web/package.json apps/web/node_modules > /dev/null && echo WEB-OK`
Expected: `WEB-OK` (only the ROOT node toolchain was removed).

- [ ] **Step 6: Commit**

```bash
git add -A .gitignore
git commit -m "chore: remove root Playwright scaffold, agent scratch, stale screenshots and DB backup

The root-level Node install existed solely to run the untouched example
spec against playwright.dev; CI replacement lands in the next commit.
apps/web keeps its own toolchain.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Real CI — pytest workflow

**Files:**
- Create: `.github/workflows/tests.yml`

**Interfaces:**
- Consumes: Task 8 (old workflow deleted).
- Produces: CI that actually runs the 471-test suite on every push/PR.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/tests.yml`:
```yaml
name: Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  pytest:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: astral-sh/setup-uv@v5
      - name: Install
        run: |
          uv venv
          uv pip install -e ".[dev,api]"
      - name: Test
        run: .venv/bin/python -m pytest -q
```

- [ ] **Step 2: Sanity-check the install extras locally**

Run: `.venv/bin/python -c "import fastapi; import pytest; print('extras-ok')"`
Expected: `extras-ok` (confirms `[dev,api]` covers `tests/test_api.py`'s FastAPI import). If tests hit the network (yfinance), they should already be mocked — the local baseline run in Task 1 proves the suite passes without live market data; if CI later reveals a network-dependent test, mark it `@pytest.mark.skipif(os.environ.get("CI") == "true", ...)` in a follow-up, don't weaken the test locally.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: run the Python test suite on push/PR

Replaces the scaffold Playwright workflow that tested playwright.dev.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Delete dead code (modeling, reporting, MarketSnapshot path, rank stub)

**Files:**
- Delete: `src/app/modeling/`, `src/app/reporting/`, `src/app/market/snapshots.py`
- Modify: `src/app/cli.py` (remove `rank` command, ~line 169), `src/app/models.py` (remove `MarketSnapshot` class, ~line 248), `src/app/aggregation/ticker_signals.py` (remove reserved-join comment, ~line 266)
- Create: one Alembic migration (autogenerated) dropping `market_snapshots`
- Test: existing suite guards the removal

**Interfaces:**
- Consumes: all prior commits (clean tree).
- Produces: `models.py` without `MarketSnapshot`; Alembic head advances past `6d0daadedcd3`.

- [ ] **Step 1: Grep-guard every deletion target**

```bash
grep -rn "from app.modeling\|from app.reporting\|import modeling\|import reporting" src apps tests scripts
grep -rn "snapshots import\|market.snapshots\|compute_snapshot_fields" src apps tests scripts
grep -rn "MarketSnapshot" src apps tests scripts
```
Expected: modeling/reporting → zero hits. `market/snapshots.py` internals → zero external call sites. `MarketSnapshot` → hits only in `models.py` itself, `market/snapshots.py`, possibly `tests/test_models.py`, and import lines. If ANY unexpected live call site appears, STOP and report instead of deleting.

- [ ] **Step 2: Delete files and the rank stub**

```bash
git rm -r --quiet src/app/modeling src/app/reporting
git rm --quiet src/app/market/snapshots.py
```
In `src/app/cli.py`: locate `def rank` (~line 169-175, prints `"rank: not implemented yet (Phase 5)"`) and remove the whole command function including its `@app.command()` decorator. In `src/app/models.py`: remove the `MarketSnapshot` class (~line 248-290) and any `__all__`/import references. In `src/app/aggregation/ticker_signals.py` (~line 266): remove the comment reserving a MarketSnapshot join "for slice D-2". In any test referencing `MarketSnapshot` (per Step 1 grep): remove those assertions/imports only — table-count assertions drop by one.

- [ ] **Step 3: Run the suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: all pass (count = baseline minus any removed MarketSnapshot-specific tests).

- [ ] **Step 4: Autogenerate + inspect + apply the migration**

```bash
.venv/bin/alembic revision --autogenerate -m "drop_market_snapshots_table"
```
Inspect the generated file: it must contain ONLY `op.drop_table('market_snapshots')` (and its downgrade re-create). Table has 0 rows (verified in audit) — no data loss.
```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```
Expected: `alembic check` reports no new upgrade operations.

- [ ] **Step 5: Commit**

```bash
git add -A src/app alembic tests
git commit -m "refactor: delete dead code — empty modeling/reporting, MarketSnapshot path, rank stub

- modeling/ and reporting/ were one-line docstring packages, never imported
- market/snapshots.py had zero call sites; market_snapshots table had 0 rows
  (dropped via migration)
- 'tsr rank' printed 'not implemented yet (Phase 5)' since May

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: ADR 0009 + final verification

**Files:**
- Create: `docs/decisions/0009-two-section-restructure.md` (via the `adr-writer` project subagent)

**Interfaces:**
- Consumes: everything above committed; `docs/superpowers/plans/2026-08-04-restructure-roadmap.md` as source material.
- Produces: the decision record Plans 2–4 cite.

- [ ] **Step 1: Dispatch adr-writer**

Dispatch the `adr-writer` subagent with this brief: *"Draft ADR 0009: restructure into two sections — Analysis (quant-first: screener, technicals/fundamentals, entry/exit research, walk-forward backtests; the YouTube-creator pipeline demoted to a pluggable, off-switchable side feature) and Portfolio Manager (manual position/trade ledger; Revolut has no API; new Position/PortfolioTrade/CashFlow tables; watchlist stays a distinct Analysis concept — watching ≠ holding). Decisions: creator strategies + per-call backtest are parked, not deleted (20 calls vs min_n_trades=30 → underpowered by construction; harness moves to Analysis with pluggable signal source); Discord adapter stays on the side-feature roadmap. This ADR amends docs/entry-exit-research-plan.md's 'position sizing out of scope' exclusion: manual position TRACKING is now in scope; auto-execution remains forbidden. Supersedes the creator-first emphasis of ADR 0005 while preserving its decision-support framing (rankings + evidence + N, user pulls the trigger). Source: docs/superpowers/plans/2026-08-04-restructure-roadmap.md."*

- [ ] **Step 2: Review the draft**

Check it follows the 0001–0008 house style (context/decision/consequences/out-of-scope), is dated 2026-08-04, and states both the ADR-0005 relationship and the entry-exit-plan amendment.

- [ ] **Step 3: Commit**

```bash
git add docs/decisions/0009-two-section-restructure.md
git commit -m "docs: ADR 0009 — two-section restructure (Analysis + Portfolio Manager)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Final verification**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -3
.venv/bin/alembic check
git status --porcelain
git log --oneline -12
```
Expected: suite green; `alembic check` clean; `git status` empty (except gitignored paths); ~10 new commits since `2afc898`. Report the final counts.
