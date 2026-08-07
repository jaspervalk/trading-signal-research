# Ticker scorecard — design

**Status:** proposed. No code written. Awaiting go/no-go.
**Date:** 2026-08-07
**Inputs:** a capability audit of this repo, and a survey of 7 commercial scorecard products plus 7 academic frameworks (`.superpowers/research/scorecard-products.md`).

---

## 1. What this page is, and what it is not

`/tickers/[ticker]` answers *"what is the price doing, and where would I enter?"* — it is a trade-setup view.

This page answers a different question: **"what kind of business is this, what has to be true for each outcome, and how much of that can I actually verify?"** It is a holding-period view. You would read it before deciding whether a name belongs in the portfolio at all, and re-read it when a thesis is under pressure.

It is explicitly **not** a rating. There is no headline number, no letter grade, no five-star anything. Section 3 explains why that is a design decision rather than a limitation.

---

## 2. What the research actually says

Seven commercial products were surveyed in depth. Four findings are load-bearing for this design.

**Composite scores destroy the information you need.** A stock reaches "B" via strong value and weak momentum, or the reverse, and the grade alone cannot tell you which. Every product publishing a single composite faces this — Simply Wall St's Snowflake, Seeking Alpha's Quant Rating, Zacks VGM, Stockopedia's StockRank. Worse, more inputs mean more vendor degrees of freedom: Seeking Alpha states its weights are "optimized to maximize predictive accuracy" against a backtest, and the weights are undisclosed, so the composite cannot be audited for overfitting from outside. The two most defensible designs reviewed were Morningstar's layered structure and **Koyfin's refusal to publish a composite at all**.

**Momentum hides inside "quality" in at least three products.** Seeking Alpha's Quant Rating has a disqualifying Momentum threshold that caps the headline rating on price action alone. Zacks Rank is architecturally revision-momentum, and revisions often follow price rather than lead it. Value Line's Technical Rank is self-admittedly price momentum with "earnings are not a factor" — just not labelled momentum. Any signal correlated with recent price must be **labelled momentum explicitly**, never folded into a durability score where it reads as something more permanent.

**Backward-looking metrics are routinely dressed as forward judgements.** ROE, five-year EPS growth, debt trend — all trailing, all presented under labels like "Future" or "Growth Score". The user cannot tell the ratio without reading a methodology PDF. This is the most common dishonesty in the category.

**Published edges decay once published.** Value Line's Timeliness Rank is a documented 40-year case: a real, academically-cited anomaly that decayed to roughly zero from the early 1990s as it became well known. Anything we invent here is exposed to the same fate, and worse, to overfitting we cannot detect from inside.

The one genuinely novel mechanical idea in the whole category is Morningstar's **uncertainty-scaled margin of safety**: a higher-uncertainty business must be cheaper before it counts as cheap. Section 6 adapts it.

---

## 3. The core decision: no composite, and no invented weights

Two rules follow directly from the research and from ADR 0005.

**No single score.** Each dimension stands alone with its components visible. There is no roll-up, no weighting, no overall grade. This is not squeamishness — it is that a weighted blend of five dimensions has a search space large enough to fit anything, and neither of us could audit it afterwards.

**Where a score is unavoidable, use one somebody else pre-registered decades ago.** Rather than invent a proprietary "financial health score" and tune it until the output looks sensible, implement **Piotroski F-Score**, **Altman Z''**, and **Beneish M-Score** exactly as published. Their thresholds were fixed in 2000, 1968 and 1999 respectively, they have decades of out-of-sample history, and — the point — *we did not fit them*. That is a far stronger epistemic position than any blend we could construct, and it is the direct answer to the overfitting trap.

Beneish is worth having for a specific reason: it flagged Enron three years before collapse on pure accrual mathematics while human analysts missed it. A narrow mechanical score can beat narrative judgement on the exact question it was built for.

---

## 4. The five dimensions

Each renders as a panel of rows in the existing `DecisionRubricEntry` shape — name, value, threshold, passed, weight, note — which the codebase already produces in `analysis/status.py` and already renders. Every row carries two tags: **provenance** (`computed` / `filed` / `model-written`) and **direction** (`trailing` / `forward`). The second tag is how this page refuses the category's most common dishonesty.

### 4.1 Trend and momentum — fully computable today

Named *momentum*, never *quality*. Sources: the ~40 metrics on `IndicatorPanel`, `MarketSnapshotPanel`, `LevelsPanel` already served by `GET /tickers/{t}/research`.

Rows: MA alignment, 50/200 slope, distance to 200-day, RSI(14), realized vol, relative strength vs SPY at 63d and 126d, distance from 52-week high, ATR%. All trailing, all arithmetic on bars.

Build cost: near zero. The metrics exist; only the panel assembly is new.

### 4.2 Financial health — published scores, computable from EDGAR

- **Piotroski F-Score** (0-9): nine binary signals across profitability, leverage/liquidity, and operating efficiency.
- **Altman Z''** (the non-manufacturer variant, since our universe is not all industrial).
- **Beneish M-Score** (8 indices): accrual quality and manipulation risk.

All three need balance-sheet series, which is the one real build item — see §7.

Each renders as its component signals, not just the total. An F-Score of 7 is uninformative; *which two failed* is the whole content.

### 4.3 Durability — the EDGAR unlock, and the honest limits

This is the dimension the research calls "moat", and where most products are weakest because it genuinely requires judgement. Morningstar employs analysts for exactly this.

What we can compute, once balance-sheet series exist:

- **ROIC, quarterly, 10 years** — NOPAT over invested capital. The inputs are present in 49-60 of 60 sampled filers.
- **ROIC persistence** — how many of the last 40 quarters cleared a cost-of-capital floor, and the trend. Persistence is the closest measurable proxy for a moat: a business that has earned excess returns for a decade against competition has demonstrated something a single-year ROIC cannot.
- **Gross-margin stability** — `gm_volatility_pp`, already computed in `supply/metrics.py`.
- **Reinvestment rate** — capex over operating cash flow, both already fetchable.
- **Dilution** — buyback spend as a USD proxy today; true share count once the unit fix lands.

What we will **not** score, stated on the page rather than silently omitted:

- **Market share.** No data source in this repo.
- **Pricing power.** Decomposing revenue growth into price × volume requires unit volumes, which XBRL does not carry. The honest substitute is showing Δmargin beside Δrevenue and letting you read it.
- **Switching costs, network effects, brand.** Not computable from any wired source. These belong to the narrative layer in §5, clearly marked as such.

### 4.4 Valuation — relative only, and paired with a quality floor

**There is no intrinsic-value model in this codebase, and this design does not add one.** The [thematic-agent design](thematic-agent.md) already audited this: a reverse-DCF on yfinance inputs (no forward FCF, no consensus beyond a single `forwardPE`, no capex guidance, no beta-to-WACC bridge) produces a number whose precision is fictional. That verdict stands.

So valuation is relative, on two axes:

- **Versus its own history** — current forward P/E, EV/EBITDA and P/S as percentiles of the company's own 10-year range. This is the axis the EDGAR history makes newly possible and it is more informative than the peer axis.
- **Versus peers** — `PeerComparison` medians, where a curated peer set exists.

The research is emphatic about one failure mode: **sector-relative cheapness misleads when an entire sector is being repriced for a structural reason.** A coal company or legacy telco screens cheap against its sector indefinitely while the sector re-rates downward together. The fix practitioners converge on is relative value **plus an independent quality floor**. So the valuation panel renders the §4.2 health scores inline, and a cheap-but-failing-Piotroski name is displayed as such rather than as a bargain.

### 4.5 Future-proofness — mostly not computable, and the page will say so

The softest dimension and the one where I would most expect a competitor to bluff.

Computable and genuinely relevant: R&D intensity versus sector, reinvestment rate, revenue CAGR over ten years and whether it is decelerating, capex intensity as a proxy for expansion lead times.

Not computable: technological substitution risk, regulatory trajectory, management quality. These are the things that actually determine whether a business survives twenty years, and no scorecard on the internet knows them either — the honest ones say so. This panel will carry an explicit note naming what it cannot see.

---

## 5. Bear / base / bull, done honestly

Most sites write three narrative paragraphs. That is unfalsifiable and it is where a language model will happily invent.

The structure here inverts it: **a scenario is a set of measurable conditions, and the prose is commentary on the conditions rather than a substitute for them.**

Each scenario carries:

- **Conditions** — specific, checkable statements with a field reference. *"Gross margin returns to its 75th percentile (28.4%, last seen 2023-Q2)"*, not *"margins recover"*.
- **Mechanical consequence** — what those conditions imply arithmetically. Reusing `earnings_torque`: if margin returns to the stated percentile on today's revenue, the resulting gross-profit change against market cap is arithmetic, not opinion.
- **Falsifier** — the observation that kills the scenario. `StyleFitPanel` already produces `invalidation_conditions`, `what_would_improve` and `what_would_weaken` per style; that scaffolding exists and is currently unused.
- **Optional prose** — one paragraph from the existing Deep-mode lens panel, tagged `model-written` and visually distinct.

`EntryExitPlan` already carries `bull_case` and `bear_case`. There is **no base case** — that is a schema and prompt change, not new infrastructure.

The base case deserves a specific definition rather than "the middle one": **current conditions persist**. No margin recovery, no deterioration, current ROIC holds. It is the null hypothesis, and having it stated makes the other two scenarios meaningful as departures from it.

---

## 6. Uncertainty, and the one idea worth stealing

Morningstar's uncertainty rating widens the discount required before a stock counts as cheap. It is the only genuinely novel mechanical idea in the category, and it adapts cleanly here because we can compute uncertainty from things we already track:

- `quarters_of_history` — thin history means wide uncertainty
- `dropped_implausible` — filings we could not parse cleanly
- `gm_volatility_pp` — earnings variability
- count of `missing` fields in `PanelDigest`
- analyst dispersion, where available

This produces an **uncertainty band, not a score**, and it does one job: it sets how far below its own historical valuation range a name must trade before the valuation panel calls it cheap. A high-uncertainty business needs a bigger discount. That is honest, mechanical, and it directly uses the freshness and coverage metadata this project already insisted on tracking.

---

## 7. What must be built

Ordered by dependency. The first item unlocks most of the rest.

**1. Balance-sheet series from EDGAR.** Balance-sheet facts are *instants* (no `start` date), so `quarterly_series` structurally cannot return them; `screen.py` has a private `_latest_instant` that returns only the newest value. Promote it into `edgar.py` as `instant_series()` returning the full dated history. This single change unlocks ROIC, debt, working capital, goodwill — and therefore §4.2 and §4.3 entirely. The concepts are already sitting in the 235 cached companyfacts files.

**2. Two small parameters on `quarterly_series`.** It reads only `units["USD"]` and only the `us-gaap` namespace. A `unit=` parameter unlocks share counts (currently unreachable, blocking true dilution); a `namespace=` parameter unlocks `dei`. Roughly five lines each.

**3. Concept chains** for operating income, tax, assets, current assets/liabilities, equity, long-term debt, R&D, capex, interest expense, SBC, goodwill. Mechanical, following the existing chain pattern.

**4. Scoring modules** — `piotroski.py`, `altman.py`, `beneish.py`, `roic.py`, `uncertainty.py`. Pure functions over the series, exhaustively testable, no network.

**5. Endpoints.** Several things are computed server-side but never exposed: `FundamentalsExtended`, `PeerComparison`, `PanelDigest`, and per-ticker `SupplyMetrics` (currently only reachable through the batch screen). The scorecard needs a single `GET /scorecard/{ticker}`.

**6. The page.** Five panels in the existing rubric style, the scenario block, the uncertainty band. Reusing `MarginHistoryChart`, which currently only appears on `/supply`.

**Suggested phasing:** (1)+(2)+(3) with tests, then (4) with published-formula test vectors, then a check that the numbers are sane on names we know, then the endpoint, then the page. The pattern from the supply screener holds: the calculation is the product, and phase 2 should not start until phase 1 produces numbers that survive inspection.

---

## 8. What this design refuses to do

- No overall score, letter grade or star rating.
- No price target, fair value estimate or entry price. Those live on the research page and stay there.
- No invented weights. Where a formula is used it is one published and fixed by someone else, decades ago.
- No "pricing power" or "market share" score, because the data does not exist.
- No LLM-generated numbers anywhere. The model may write commentary on computed conditions; it may not produce a condition.
- No sector-relative valuation without a quality floor beside it.

---

## 9. Open questions

1. **Is five dimensions too many?** Trend, health, durability, valuation, future-proofness. I would consider dropping future-proofness (§4.5) entirely on the grounds that it is 80% not computable and the honest version is three metrics and a disclaimer. Its main value may be as a place to *record* what you cannot measure.

2. **Cost of capital for ROIC persistence.** A fixed 8% hurdle, a per-sector hurdle, or CAPM from beta? Fixed is crude but auditable and cannot be tuned; CAPM imports a beta from yfinance that I do not fully trust. I lean fixed, stated on the page.

3. **Universe scope.** EDGAR is US filers only. Foreign private issuers on 20-F/40-F have sparse XBRL, so ASML.AS and RHM.DE — both in your target weights — would show a health and durability panel that cannot be populated. Do we show the page with those panels explicitly unavailable, or restrict it to US names?

4. **How much narrative?** The scenario block can run with zero LLM involvement (conditions and arithmetic only), or with one paragraph per scenario at roughly Deep-mode cost. I lean toward shipping it with none, then adding prose once the computed half is trusted.

5. **Relationship to `/tickers/[ticker]`.** Separate page, or a second tab on the existing one? Separate keeps the trade view uncluttered; a tab keeps one URL per ticker. I lean separate, linked both ways.
