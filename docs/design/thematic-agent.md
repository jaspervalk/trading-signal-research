# Thematic / supply-chain research agent — design evaluation

**Status:** evaluation only. No implementation. Awaiting go/no-go.
**Date:** 2026-08-05
**Author:** design pass over the existing Deep-research stack.

---

## 0. Three findings that change the proposal before we start

**(a) The premise "the agent does not see the fundamentals, technicals, or valuation panels" is not accurate as stated.** The `ResearchPacket` carries the whole `TickerResearchView` ([context.py:45-61](../../src/app/research/context.py#L45-L61)), and two of the four Deep analysts already receive valuation:

- **Fundamental** gets 14 `ValuationPanel` fields plus the full `FundamentalsExtended` and `PeerComparison` blocks ([fundamental.py:125-151](../../src/app/research/agents/fundamental.py#L125-L151)).
- **Contrarian-Risk** already receives technicals *and* valuation together ([contrarian.py:92-126](../../src/app/research/agents/contrarian.py#L92-L126)) — it is, today, the only lens doing the reconciliation the proposal asks for.

The real gap is narrower and more interesting: **the judge — which writes the plan — is valuation-blind.** Its user template interpolates status, setup, action, style, `last_close`, `atr_14`, 52-week distances, the candidate-level block and the lens block, and nothing else ([judge.py:236-253](../../src/app/research/agents/judge.py#L236-L253)). Valuation reaches the final plan only as prose inside a lens summary. So "valuation already prices the story in, technicals are deteriorating" *can* be formed by Contrarian-Risk today, but it reaches the output only if that lens happens to say it in ≤25 words, and the judge cannot check it against a number.

That reframes the cheapest version of this feature: it is not "pass the panels to the agent", it is "let the synthesis step see valuation, and give one lens an explicit reconciliation remit."

**(b) No valuation model exists. At all.** An exhaustive grep for `dcf|discounted cash|wacc|terminal|intrinsic|fair.?value|npv|implied growth|implied multiple|reverse dcf|exit multiple|owner earnings|residual income` across `*.py`, `*.ts`, `*.tsx`, `*.md` returns zero implementations. The only hits are the word "implied" used for the *implied action label* (BUY/HOLD/…) in [action.py](../../src/app/analysis/action.py), and one aspirational line in a vendored reference doc. Valuation today is a **descriptive multiples pass-through** from `yfinance .info` plus hand-curated peer medians.

Your requirement 2 asks for implied assumptions — growth, margin, exit multiple — behind a claim like "the valuation is way off even for this future." **That is not buildable against the current valuation module.** It requires a new one. Section 5.4 specifies what a defensible minimum looks like and section 3 argues against the ambitious version.

**(c) The specific claim type in your MP example has no data source in this system.** "Company X derives N% of revenue from magnet materials" is segment-revenue data. `yfinance .info` does not carry segment revenue; neither does `.financials`. There is no filings parser, no XBRL, no transcript-of-earnings source. That number can only ever come from a web search that happens to surface a filing or an article — i.e. from the least verifiable input in the system. Any design must treat revenue-exposure percentages as *sourced quotes*, never as computed facts, or it will manufacture them.

---

## 1. What the current agent actually does

### Entry points

| Path | Function | Cost / latency (measured, n=35 rows in `research_plans`) |
|---|---|---|
| Quick | [quick.py:230 `run()`](../../src/app/research/quick.py#L230) — one Haiku call, `temperature=0`, `max_tokens=16000` | **$0.0119 avg**, 18.4s avg (docs claim ~$0.01, 3-5s) |
| Deep | [deep.py:57 `run()`](../../src/app/research/deep.py#L57) — 4 parallel Haiku analysts → optional debate round → Sonnet judge | **$0.0616 avg**, 34.0s avg (docs claim ~$0.10, 30-45s) |

Both are exposed at `POST /research/{quick,deep}/{ticker}` ([research.py:143-253](../../apps/api/app/routes/research.py#L143-L253)) and share the `EntryExitPlan` output schema.

### Orchestration loop (Deep)

1. **Fan out** four analysts in parallel via `run_agents_parallel` (ThreadPoolExecutor, `max_workers=4`, results slotted by submit index so order is deterministic — [base.py:357-386](../../src/app/research/agents/base.py#L357-L386)). A failed analyst degrades to `AgentResult(lens=None, error=...)`; the run continues on survivors. All four failing raises ([deep.py:80-86](../../src/app/research/deep.py#L80-L86)).
2. **Optional cross-lens debate** (`research.deep.cross_lens_round`, ON by default since 2026-08). Each analyst sees the other three's round-1 reads and may revise ([deep.py:101-154](../../src/app/research/deep.py#L101-L154)).
3. **Record lens snapshots** for later accuracy scoring ([lens_recording.py](../../src/app/research/lens_recording.py), unique on `(ticker, as_of, mode, lens_name)`).
4. **Sonnet judge** synthesises and picks levels ([judge.py:174-206](../../src/app/research/agents/judge.py#L174-L206), `claude-sonnet-4-6`, `max_tokens=3500`, `temperature=0`).

### The four lenses and what each actually receives

| Lens | Model / tokens | Receives | Does **not** receive |
|---|---|---|---|
| `quantitative` | Haiku, 1500 | 9 market fields, 11 indicator fields, 7 level fields, setup type+confidence | valuation, claims, **candidate levels** (despite its docstring claiming otherwise, [technical.py:3](../../src/app/research/agents/technical.py#L3)) |
| `fundamental` | Haiku, 2000 | 14 valuation fields, full `FundamentalsExtended`, full `PeerComparison` | all technicals, all levels, claims, candidate levels |
| `sentiment_macro` | Haiku, 2500, **+web_search** | 4 valuation fields (sector, industry, beta, days-to-earnings), full `TranscriptContext`, claim bodies | technicals, levels, fundamentals, peers, candidate levels |
| `contrarian_risk` | Haiku, 1500 | 4 indicator + 6 market + 2 level fields, **10 valuation fields**, status/setup/action/style | candidate levels (yet is told to quantify downside "in R units"), claim *bodies* (gets a count only) |
| `judge` | **Sonnet**, 3500, T=0 | status, setup, action, style, `last_close`, `atr_14`, 52w distances, candidate-level block, lens block | **all valuation**, most indicators, **all of `LevelsPanel`**, claims, fundamentals, peers |

### The hallucination guard (load-bearing — do not weaken)

The judge **never emits prices.** It emits `entry_kind` (a categorical) plus 0-based **integer indices** into precomputed candidate lists, resolved verbatim in Python ([judge.py:431-460](../../src/app/research/agents/judge.py#L431-L460)), with indices clamped to range and the invalidation re-snapped if it is not strictly below the entry low ([quick.py:673-697](../../src/app/research/quick.py#L673), `SNAP_ATR_FRACTION = 0.75`).

Candidates themselves are deterministic and **purely technical** ([exits.py:9-15](../../src/app/research/exits.py#L9-L15)): nearest resistance + 0.25×ATR, the 63-bar high, and Fibonacci 1.272/1.618 extensions; runners at +1.5×ATR capped at the next swing high. Degenerate bands (>5×ATR or <0.05×ATR wide) are dropped ([exits.py:104-120](../../src/app/research/exits.py#L104-L120)).

**There is no valuation-derived level anywhere in the candidate set.** This is the crux of question 3.

### Storage, caching, costing

- **Cache key** `(ticker.upper(), mode, day_key)` where `day_key` is the UTC calendar date ([cache.py:22-25](../../src/app/research/cache.py#L22-L25)). No TTL; invalidation is implicit day rollover. `?force=true` skips the read but does not delete.
- **No unique constraint** on `research_plans` — only a non-unique composite index ([models.py:882-884](../../src/app/models.py#L882-L884)). The table is append-only and reads take `max(id)`. The live DB has up to 6 rows for the same key.
- **A plan is only cached if `plan.lenses` is non-empty** ([research.py:241-244](../../apps/api/app/routes/research.py#L241-L244)) — a deliberate guard against poisoning the day with a truncated response.
- **Costing**: `HAIKU_PRICE_IN/OUT = 1.0/5.0`, `SONNET_PRICE_IN/OUT = 3.0/15.0`, `_WEB_SEARCH_PRICE_PER_SEARCH = 0.01` ([base.py:25-28,310](../../src/app/research/agents/base.py#L25-L28)). Cache-read/cache-write token buckets are **ignored** — prompt caching is unused today. There is **no budget cap** anywhere, despite the plan doc calling for one.
- **No cache in the system is keyed by anything other than a single ticker.** The one multi-ticker path, `scan_rerank`, is deliberately uncached and unpersisted ([research.py:310-311](../../apps/api/app/routes/research.py#L310-L311)). A theme-scoped cache is entirely new surface.

---

## 2. The strongest case for building this

**The judge is valuation-blind, and that is a real defect.** The system's own framing — decision support with evidence — is undermined when the synthesis step cannot see whether the price already reflects the story. Today a Deep plan can rate a setup "high confidence" on technical grounds while the Fundamental lens quietly said "rich vs peers on every multiple," and the judge has no numeric handle on that tension. Fixing this is worth doing whether or not the thematic layer ships.

**Reconciling narrative against price is the actual decision you make.** Every other panel answers "what is true now." None answers "what is already paid for." That is a genuine gap in the product, not a nice-to-have — and it is the question that distinguishes a thesis from a trade.

**The infrastructure to do this well already exists.** Parallel lens fan-out with partial-failure tolerance, forced-tool structured output, index-based level picking, per-lens accuracy recording against forward returns, cost attribution per agent. A fifth lens is a small increment on proven machinery, not a new system.

**The accuracy-measurement loop is already built and is currently idle.** `LensSnapshot` → `LensOutcome` → Wilson-CI scorecards exists end to end and has **0 outcome rows** because the scoring job has never run. A thematic lens is exactly the kind of claim that needs measurement, and the apparatus is sitting there. (Turning it on is a prerequisite, not a side benefit — see §6.)

**Peer medians are already computed and unused by the judge.** `PeerComparison` gives a defensible external anchor for "is this multiple rich" without inventing a valuation model. It reaches one lens and dies there.

---

## 3. The strongest case against

### 3.1 The core failure mode: a fluent, plausible, wrong supply-chain thesis

This is the reason to be cautious, and it deserves to be stated precisely rather than hedged.

Supply-chain claims have the worst possible properties for an LLM: they are **compositional** (A supplies B, B sells to C, therefore A is exposed to C's demand), **rarely contradicted in training text**, and **expensive to verify**. Each hop is individually plausible; the chain is what is wrong. A model asked "who supplies rare-earth magnets for robotics actuators" will produce a confident, well-formed list. Some of it will be right. The wrong entries will not look different from the right ones.

Three specific ways this bites here:

**Confirmation laundering.** You arrive with "robotics is the next wave." The agent, given that framing, will find supporting structure — because supporting structure exists for almost any theme. What it will not reliably do is surface the disconfirming fact that kills the thesis (a customer concentration being unwound, a substitution path, a competitor's capacity coming online). The output *reads* like research but functions as a mirror. This is worse than no research: it converts a hunch into apparent evidence and increases position size.

**False precision on unavailable data.** As established in §0(c), revenue-exposure percentages have no source in this system. The model knows what such numbers look like. Asked for one, an unconstrained schema will produce "roughly 60-70% of revenue" and it will be fluent. That number then flows into a judge that is already inclined to weigh specific figures over vague ones.

**Numeric authority laundering.** If we let the thematic layer produce a price level, a wrong narrative acquires a decimal point. A reverse-DCF built on `yfinance` annual statements — where `fcf_ttm` is [documented as misnamed and actually the most recent fiscal year](../../src/app/market/fundamentals.py#L89), where there is no analyst consensus beyond a single `forwardPE`, no segment detail, and no capex guidance — will emit a number with two decimal places that is precise and unfounded. That is strictly worse than the current honest refusal to produce one.

**What it costs you in real decisions.** Concretely: a fabricated supply-chain link plus a confident level turns a "watch it" into an entry, at a size justified by the apparent depth of the analysis. One such trade at 2-3% of portfolio, entered 20% above where the thesis actually deserved, is a multi-hundred-euro error — and the second-order cost is worse: it contaminates the trustworthy panels. Right now, when `TechnicalsCard` says RSI is 71, that is arithmetic on bars you can audit. If the same page carries a fluent supply-chain paragraph at the same visual weight, you lose the ability to tell at a glance which parts of the page are computed and which are generated. That is the real risk to this codebase, whose entire design philosophy — deterministic candidates, evidence quotes with substring validation, Wilson CIs with sample sizes, no composite scores — is about keeping that line visible.

### 3.2 Secondary objections

**The theme layer has no ground truth and therefore no evaluation.** Every other model output here is scoreable against forward returns. "Robotics is the next AI wave" is not falsifiable on a 5-day horizon, or a 21-day one. We would be adding the one component of the system that cannot be measured — into a codebase whose stated principle is that negative results get reported the same way as positive ones.

**It widens the remit of a feature whose current remit is already loosely enforced.** No JSON schema in the system enforces any length constraint — every "≤25 words", "≤350 characters", "3-5 bullets" lives in prose only, and the tool schema for `bull_case` permits 8 items where the prompt says 3-5. The system has already been bitten by unconstrained output twice (the char-splitting bug that produced 1746-character entries, and the XML-in-`bull_case[0]` regression that needed [a regex repair layer](../../src/app/research/quick.py#L466-L531)). Adding free-text thematic prose to that surface, without first tightening it, is asking for the same class of bug with worse content.

**Data staleness makes "the valuation already prices it in" unreliable today.** `ValuationPanel` comes from an `@lru_cache(maxsize=256)` with **no TTL and no `cache_clear()` call anywhere in production code** ([research.py:85-100](../../src/app/analysis/research.py#L85-L100)). In a long-lived FastAPI worker, `forward_pe` can be arbitrarily old, and the panel carries no `fetched_at` for anyone to notice. Reasoning about whether price has moved ahead of fundamentals, using a possibly-weeks-old multiple, is unsound. **This must be fixed before the feature, not with it.**

**Opportunity cost.** Plan 2 of the restructure (one market-data client, one universe module, the screener getting an API and UI) is unstarted. The screener — the actual idea-generation surface — currently has no UI at all. If the underlying want is "help me find names," fixing the screener is a cheaper and more honest path than a thematic LLM.

---

## 4. Resolving your four questions

### 4.1 Direction of reasoning — idea generator or ticker-page enrichment?

**They are two products, and your example is ambiguous about which you want.** The narrative runs top-down (theme → supply chain → MP), but the invocation you describe is "when the selected ticker is for example MP" — the ticker is already chosen. So the trace is a *top-down explanation of a bottom-up selection*.

That distinction matters because the two have different search spaces, cache boundaries, failure modes, and evaluability:

| | Idea generator (theme → tickers) | Enrichment (ticker → thematic context) |
|---|---|---|
| Search space | Unbounded — every company on earth | One ticker |
| Hallucination surface | Maximal: the mapping *is* the product | Bounded: the mapping is checkable against one company |
| Cache | Theme-keyed, shared | Ticker-keyed, per-day (existing) |
| Evaluable? | Only against forward returns of a basket you didn't hold | Against the same lens-accuracy loop as the other four |
| Fits existing architecture | No — new surface end to end | Yes — a fifth lens |

**Recommendation: build the enrichment direction only.** Reasons: it fits the existing per-ticker plan/cache/accuracy machinery; its claims are checkable against panels you already trust; and it is the direction your actual invocation implies.

**Can one agent serve both? Technically yes, and it should not.** But the design below deliberately makes the *future* idea-generator cheap: the theme brief is built as a **separately cached, ticker-independent artifact** with an explicit `theme → company` link table. Inverting that index later ("which tickers touch theme X") is a query, not a new agent. That is the right way to keep the option open without paying for it now.

### 4.2 Data contract — what gets serialized

**The good news:** one clean root exists. `TickerResearchView` is Pydantic, JSON-stable by design, and every ticker-page panel is inside it ([schema.py:327-354](../../src/app/analysis/schema.py#L327-L354)). It is already returned by `GET /tickers/{ticker}/research`.

**Four problems must be solved before serializing it to a model:**

1. **Units are inconsistent and unannotated.** Three conventions coexist: fractions (most growth/margin/return fields), **percent-already-applied (`dividend_yield` alone** — `.info` returns it pre-multiplied; the web layer applies a `v < 0.05 ? v*100 : v` heuristic that the server does not), and absolutes (`market_cap`, prices, share counts). `rsi_14` is a 0-100 index. `debt_to_equity` is a raw ratio in `FundamentalsExtended` but the screener divides the same-named `.info` field by 100. **A model will get this wrong by 100× unless each field carries an explicit unit tag.**
2. **Two untyped dicts break strict schema generation**: `SetupClassification.supporting_metrics: dict[str, Any]` and `StyleFitItem.relevant_levels: dict[str, float|None]`, both with per-branch key sets.
3. **`FundamentalsExtended` and `PeerComparison` are frozen dataclasses, not Pydantic**, and their most useful fields are `@property` (`revenue.quality`, `operating_margin_trajectory`, `balance_sheet_strength`, `capital_allocation`, `forward_pe_relative`, `growth_relative`, `margin_relative`). These **vanish under `dataclasses.asdict()`** and must be explicitly materialised.
4. **No freshness stamp.** `ValuationPanel` has no `fetched_at`. Combined with the infinite `lru_cache`, nothing downstream can tell whether a multiple is minutes or weeks old.

**Proposed contract** — a new `PanelDigest`, built server-side, passed to the thematic lens and (critically) to the judge:

```python
class Measure(BaseModel):
    """One serialized panel field, self-describing so the model cannot
    misread its unit or its age."""
    field: str            # dotted path, e.g. "valuation.forward_pe" — used as a citation key
    value: float | str | None
    unit: Literal["ratio", "fraction", "percent", "usd", "shares",
                  "days", "index_0_100", "price", "categorical"]
    as_of: datetime       # when this specific value was fetched, not when the run started
    stale_after_days: int # advisory; the model is told to discount beyond this

class PanelDigest(BaseModel):
    ticker: str
    measures: list[Measure]         # flat, ~45 entries, every one citable by `field`
    missing: list[str]              # fields that are None, named explicitly so absence is visible
```

Flat and citable is the point: the output schema (§5.5) requires every numeric claim to name a `field` that **must exist in `measures`**, validated in Python. A claim referencing a field not in the digest is rejected, not merely discouraged.

**On implied assumptions — the honest answer to your question.** You asked for the schema or a statement that it isn't buildable. **It is not buildable against the current valuation module.** There is no DCF, no reverse-DCF, no implied-growth solver, no discount rate, no terminal-value machinery anywhere in the codebase (§0b). Supporting "the valuation is way off even for this future" requires building a valuation model from scratch. §5.4 specifies the minimum defensible version; §3.1 argues the ambitious version is actively harmful on this data.

### 4.3 Deriving the entry price

**Recommendation: multiple-on-forward-estimate, computed deterministically in Python, emitted as a band, added to the existing candidate list — with the LLM still picking by index.**

Rejecting the alternatives, with reasons:

- **Reverse-DCF solved for a target IRR** — theoretically the right instrument, and the one I'd want. Rejected on input quality. It needs forward free-cash-flow, a discount rate, and a terminal assumption. What exists is: annual (not TTM) cash flow from `yfinance`, [a field literally misnamed `fcf_ttm` that holds the latest fiscal year](../../src/app/market/fundamentals.py#L89), no analyst consensus beyond a single `forwardPE`, no segment detail, no capex guidance, and no beta-to-WACC bridge. A reverse-DCF on those inputs produces a number whose precision is entirely fictional — the numeric form of exactly the failure mode in §3.1. For a company like MP specifically (capital-intensive, pre-scale, single-digit or negative margins), it is close to meaningless.
- **Technical support confluence** — already exists; that is what `exits.py` does. Adding it again is not new information.
- **A blend** — rejected on reproducibility. A weighted mix of a technical level and a valuation level cannot be audited: you can't tell which half moved the answer, and the weights would be arbitrary.

**The method, precisely.** All inputs exist today:

```
forward_eps        = last_close / valuation.forward_pe          # inverts the multiple
anchor_multiple    = peer_comparison.median_forward_pe          # external, already computed
valuation_level    = forward_eps × anchor_multiple
band               = [valuation_level × 0.95, valuation_level × 1.05]
```

Emitted as a `ZoneBand` with `method="forward EPS × peer-median forward P/E"` and a `rationale` naming both inputs and their `as_of`. Three scenario variants are produced — peer-median, peer-25th-percentile, and the stock's own forward P/E — so the output is a **band across stated assumptions, not a point estimate**.

Why this is defensible: every input is an existing field with known provenance; the arithmetic is one line and reproducible by hand; the assumption (that this company should trade at its peer group's forward multiple) is **stated, contestable, and visible** rather than buried in a model. When it doesn't apply — negative or missing `forward_pe`, no peer set, `peer_set_available=False` — it emits **nothing** rather than a fallback. Roughly: unprofitable and pre-revenue names get no valuation level, which is the correct behaviour.

**Hard guards:**
- The valuation band enters `CandidateLevels` as an additional, **clearly labelled** candidate. The judge still picks an index. The LLM never emits a price. The existing guard is preserved exactly.
- If a valuation candidate is picked, the plan must carry the assumption set that produced it (`anchor_multiple`, `forward_eps`, both `as_of` stamps) as structured fields, not prose.
- `ZoneBand` needs a `source: Literal["technical", "valuation"]` discriminator so the UI can render them differently. Mixing a technical stop with a valuation target without labelling which is which would be a regression in honesty.

### 4.4 Caching boundary

Three tiers, split on how fast the underlying truth changes:

| Tier | Key | TTL | Contents | Cost |
|---|---|---|---|---|
| **Theme brief** | `(theme_slug, week_key)` | 7 days | Supply-chain map + sourced links, ticker-independent | Expensive (~$0.05-0.08, web-search heavy), amortised across every ticker touching the theme |
| **Theme↔ticker link** | `(theme_slug, ticker, week_key)` | 7 days | The specific evidence tying this company to this theme, with citations | ~$0.005, reused across the week's runs for that ticker |
| **Per-ticker plan** | `(ticker, mode, day_key)` | 1 day (existing) | The plan, including thematic lens output | Existing |

The split sits where it does because a supply chain does not change daily but a price does. Re-running theme research on every ticker load would be the single biggest cost mistake available here.

**Two implementation warnings from the audit:**

1. **No cache in this codebase is keyed by anything but a ticker.** `research_plans.ticker` is `String(16) NOT NULL`. A theme-scoped table is new surface — do **not** try to reuse `research_plans` with a sentinel ticker.
2. **Do not inherit the existing table's semantics.** `research_plans` has *no unique constraint*, is append-only, and reads take `max(id)` — the live DB has up to 6 rows per logical key. New theme tables should have a real `UniqueConstraint` and an upsert.

---

## 5. Smallest version worth shipping

### 5.1 v1 scope

Three changes, in dependency order. **The first two are worth doing even if the thematic layer is never built.**

**Step 1 — Fix the foundations (no LLM changes).**
- Add `fetched_at` to `ValuationPanel`; give `_resolve_metadata` and `_resolve_next_earnings` a real TTL instead of an unbounded `lru_cache`.
- Build `PanelDigest` (§4.2) with unit tags and explicit `missing` list.
- Add schema-level `maxLength`/`maxItems` to the existing tool schemas so the prose constraints become real.

**Step 2 — Give the judge eyes (no new agent).**
Pass `PanelDigest` to the judge and require that its `bull_case`/`bear_case` cite `field` keys. This alone closes the §0(a) gap. Measurable, cheap (+~$0.005/run), and independently valuable.

**Step 3 — One thematic lens, enrichment direction only.**
A fifth analyst, `thematic`, receiving `PanelDigest` + the cached theme brief, with a reconciliation remit: *does the narrative justify the multiple the market is already paying?* Plus the deterministic valuation candidate from §4.3.

### 5.2 Explicitly out of scope for v1

- **The idea-generator direction** (theme → candidate tickers). Deferred; the link table keeps it cheap later.
- **Reverse-DCF / implied-assumption solving.** Not on this data (§4.3).
- **Any claim about revenue exposure by segment** unless it arrives as a sourced quote with a URL (§0c).
- **Multi-hop supply chains.** v1 permits **one hop** — theme → input → company. "A supplies B, B sells to C, therefore A benefits from C" is where compositional error compounds; it stays out until single-hop accuracy is measured.
- Auto-refreshing themes on a cron; theme discovery (themes are user-supplied in v1); portfolio-level thematic exposure; anything touching position sizing (forbidden by ADR 0005 §4 / 0008 §4 / 0009).

### 5.3 Orchestration

```
gather(ticker, with_deep_extras=True)      # existing
   ├─ PanelDigest.build(view, fundamentals, peers)      [new, deterministic]
   ├─ theme_brief = cache.get(theme_slug, week) or build_theme_brief(...)   [new, cached]
   └─ candidate_levels + valuation_candidates            [new, deterministic]

run_agents_parallel([quantitative, fundamental, sentiment_macro,
                     contrarian_risk, thematic])         # 4 → 5, still parallel
   → optional cross-lens round (existing)
   → judge(lenses, candidate_levels, PanelDigest)        # judge gains the digest
```

The thematic lens runs **in the existing parallel fan-out**, so wall-clock is unchanged (`max_workers` 4 → 5). It does **not** perform its own web search — it consumes the cached brief. This is what keeps latency flat.

### 5.4 The theme brief (the only web-search component)

Built once per theme per week by a Sonnet call with `web_search` (`max_uses` 5-8). Its output is deliberately **not prose**:

```python
class SourcedLink(BaseModel):
    subject: str                 # "permanent magnets"
    relation: Literal["input_to", "supplies", "competes_with", "substitutes_for"]
    object: str                  # "robotic actuators"
    quote: str                   # verbatim ≤200 chars from the source
    url: str
    title: str
    retrieved_at: datetime

class ThemeBrief(BaseModel):
    theme_slug: str
    hypothesis: str              # the unfalsifiable framing, quarantined here
    links: list[SourcedLink]     # every structural claim, each with a URL
    companies: list[ThemeCompanyLink]   # ticker + which links justify inclusion
    falsifiers: list[str]        # what would make this theme wrong
    built_at: datetime
```

`hypothesis` is the *only* free-text field, it is exactly one string, and it is rendered under a "Hypothesis — not evidence" heading. Everything structural must be a `SourcedLink` with a URL and a verbatim quote. This mirrors the extraction pipeline's evidence-quote requirement (ADR 0002), which is the proven pattern in this repo for exactly this problem.

### 5.5 Output schema — making unsourced claims structurally impossible

Your constraint 4.1 asks for structural impossibility rather than discouragement. Two mechanisms, both enforced in Python after the tool call:

```python
class Evidence(BaseModel):
    """A checkable statement. The discriminated source is required."""
    statement: str
    source: PanelSource | WebSource | ClaimSource   # discriminated union, no default

class PanelSource(BaseModel):
    kind: Literal["panel"]
    field: str          # MUST resolve in PanelDigest.measures — validated
    value: float | str | None

class WebSource(BaseModel):
    kind: Literal["web"]
    url: str
    quote: str          # MUST be a substring of the retrieved page text — validated

class ClaimSource(BaseModel):
    kind: Literal["claim"]
    claim_id: int       # MUST exist in the DB

class ThematicLens(LensView):
    hypotheses: list[str]        # unfalsifiable framing; MAY NOT contain digits
    evidence: list[Evidence]     # every checkable statement, each sourced
    reconciliation: str          # the narrative-vs-price read
    falsifiers: list[str]        # required, min 1
    confidence: Literal["low", "medium", "high"]
```

Enforcement, all post-hoc in Python — not trusted to the model:
1. `PanelSource.field` must exist in `PanelDigest.measures`, else the evidence item is **dropped** and the drop is logged.
2. `WebSource.quote` must substring-match retrieved text — the same validator pattern as [the extraction validator](../../src/app/extract/validator.py).
3. **`hypotheses` entries containing digits are rejected.** This is the blunt instrument that stops "robotics will be a $200B market by 2030" from being filed as a hypothesis to dodge sourcing.
4. `falsifiers` must be non-empty or the lens is discarded.
5. A lens whose `evidence` list is empty after validation contributes **nothing** to the judge — it does not degrade to prose.

The separation you asked for in constraint 4.2 is then structural: `hypotheses` and `evidence` are different types in different arrays, rendered under different headings, and only one of them can carry numbers.

### 5.6 Cost and latency

| | Today | v1 warm theme | v1 cold theme |
|---|---|---|---|
| Cost | $0.062 | ~$0.085 (**1.4×**) | ~$0.15 (**2.4×**, once per theme per week) |
| Latency | 34s | ~36s (5th lens absorbed by parallel fan-out) | ~60s (web searches serialise) |

Amortisation is what keeps this honest: a theme brief costing ~$0.07 used across 5 tickers in a week adds ~$0.014/run. **Within the same order of magnitude, as required.** Two things must land with it: prompt caching (currently unused — `_compute_cost` ignores cache-token buckets entirely) and the budget cap the plan doc has always specified and no code has ever implemented.

---

## 6. Evaluation approach

**A good run**, on the MP example: the thematic lens returns 1 hypothesis ("robotics demand scales actuator and magnet consumption"), 3-5 `Evidence` items each with a URL and verbatim quote or a `panel` field key, a reconciliation naming *specific* multiples from the digest ("forward P/E 41 vs peer median 18 — the multiple already embeds several years of the demand case"), 2+ falsifiers, and confidence bounded by data availability. If segment revenue can't be sourced, it says so rather than estimating.

**A bad run** asserts a supply relationship without a URL; cites a revenue-exposure percentage with no source; states the theme as fact rather than hypothesis; produces a reconciliation with no numbers in it; or returns zero falsifiers.

**Can quality be scored without you reading every output? Partially — and the split matters.**

*Automatable (should gate every run):*
- **Sourcing rate** — fraction of `Evidence` surviving validation. Below ~0.8 means the lens is inventing; alert.
- **Quote-match rate** — substring validation, binary per item. This is already proven infrastructure (ADR 0002, `data/gold/extraction_gold.jsonl`).
- **Digit leakage** into `hypotheses` — should be zero by construction; a non-zero rate means the schema is being routed around.
- **Field-key validity** — `PanelSource.field` resolution rate; should be 1.0.
- **Directional accuracy via the existing loop** — `LensSnapshot` → `LensOutcome` at 1/3/5/21d with Wilson CIs, exactly as the other four lenses. The thematic lens gets a scorecard next to them, and it should be held to *beating* the existing four before it earns weight.

*Not automatable (needs you, sampled):*
- Whether the supply-chain link is **true**. A URL proves a source said it, not that it's correct.
- Whether the reconciliation is *insightful* rather than merely arithmetic.

**Proposed protocol**: a 20-ticker gold set of thematic reconciliations that you grade once (thesis-real / thesis-fabricated / thesis-unverifiable), stored beside the extraction gold set. Run it before and after any prompt change. This is the same pattern the extractor already uses and it is the only honest way to detect the §3.1 failure mode — automated metrics measure *sourcing discipline*, never *truth*.

**A hard precondition.** `lens_outcomes` currently has **0 rows** against 136 snapshots — the scoring job has never run. Shipping a fifth lens into a measurement loop that has never executed would mean adding an unmeasurable component to an unmeasured system. **Run the scoring job and get scorecards for the existing four lenses first.** If the current four can't be shown to add value, a fifth is premature.

---

## 7. Open questions

1. **Which direction do you actually want?** §4.1 argues your example is enrichment wearing a top-down narrative. If what you really want is "surface names I haven't thought of," the honest answer is that **the screener is the right tool and it currently has no UI** — that would be cheaper and more reliable than a thematic LLM.
2. **Where do themes come from in v1?** I've assumed you supply them (`--theme robotics-actuators`). The alternative — the agent proposes themes — multiplies the hallucination surface and has no ground truth. Confirm you're happy typing the theme.
3. **Is a valuation level worth having at all if it only works for profitable companies with a peer set?** The method in §4.3 emits nothing for pre-profit names — which includes many of the names a robotics/rare-earth theme would surface. That may make it useless exactly where you want it. If so, we should ship the thematic lens *without* a valuation level and keep levels purely technical.
4. **How much staleness will you tolerate on a theme brief?** I've proposed 7 days. A fast-moving theme (export controls, a policy change) can invalidate a brief in a day.
5. **Do you want the thematic lens to influence the plan, or sit beside it?** Feeding it to the judge lets it move levels and confidence. Rendering it as a separate panel keeps it strictly advisory and quarantines the §3.1 risk. **I lean strongly toward advisory-only for v1** — earn influence by scorecard.
6. **Budget cap** — there is none anywhere today, despite the plan doc specifying `monthly_budget_usd: 30`. Should Step 1 include it? This feature is the first that could run up a bill without you noticing.

---

## Recommendation

**Build Steps 1 and 2. Defer Step 3 pending the answers above.**

Steps 1 and 2 — freshness stamps and a real TTL on valuation, the `PanelDigest` with unit tags, schema-level length limits, and passing the digest to the judge — fix a genuine defect (§0a: the judge is valuation-blind), are entirely deterministic, add ~$0.005 and no latency, and make the system better whether or not the thematic layer ever ships. They are also strict prerequisites for doing Step 3 safely.

Step 3 is the interesting idea and the risky one. I'd want three things true before starting it: the lens-accuracy job actually running so a fifth lens can be measured (§6), your answer on advisory-vs-influential (Q5), and your answer on whether a valuation level that only works for profitable companies earns its keep (Q3). If the answer to Q1 turns out to be "I want new names," I'd redirect the whole effort to giving the screener a UI instead — it's a smaller build with no hallucination surface at all.

The thing I'd most want you to weigh: this codebase's distinguishing quality is that a reader can tell computed facts from generated prose at a glance. Every design decision in it — deterministic candidates, evidence quotes validated by substring, Wilson CIs with sample sizes, no composite scores — protects that line. A thematic layer is the first feature that puts fluent, unverifiable, decision-shaped text on the same page as arithmetic. It can be done safely, and §5.5 is my best attempt at how; but the schema has to carry that weight, because the prose won't.
