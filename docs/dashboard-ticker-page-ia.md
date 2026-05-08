# Ticker page — information architecture (frontend-agnostic)

This doc fixes the *content* of the ticker-first dashboard page before any styling decisions are made. It is the spec a designer (or Huashu-Design) reads to produce mockups; it is the spec the FastAPI routes are built against. Visual hierarchy and styling are intentionally NOT in scope here.

Per [ADR 0005](decisions/0005-product-pivot-decision-support.md), the ticker page is the **headline view** of the dashboard. The user opens AAPL, sees everything that the system knows about AAPL, and walks away with a defensible "decision-support read" — never a buy/sell label.

## Sections (top to bottom)

### 1. Header strip
**Purpose:** identify the ticker, set the disclaimer tone immediately.
**Content:**
- Ticker symbol (large, mono).
- Current price + day change (from the most recent bar; can be "—" if no data).
- Watchlist toggle (existing `WatchlistButton`).
- Subtle link to [docs/disclaimer.md](disclaimer.md): *"Decision support, not advice — see methodology."*

### 2. Live-signal strip (NEW — backed by TickerSignal)
**Purpose:** answer "is this ticker getting talked about right now?" in two seconds.
**Content (one row of compact tiles, 3 columns × 2 rows of signal types):**
- For each `(window_size, signal_type)` from TickerSignal:
  - Window: `7d` and `30d` (skip `1d` — usually empty for our cadence).
  - Signal type: `claims_all`, `claims_factual`, `creator_consensus`.
  - Each tile shows: `n_mentions`, `n_distinct_creators`, `net_polarity` (color-coded ±1), `credibility_weighted_polarity`.
- Empty state: tile shows "no signal" with N=0, not blank.

**Data source:** `GET /tickers/{ticker}/signals`. Aggregates already materialized per ADR 0006.

### 3. Price chart with overlays
**Purpose:** put extracted calls in market context, visually.
**Content (existing component):**
- Daily candles, last 365 days.
- Markers at each call's `posted_at`, color = 5d return after the call.
- Add: small dotted vertical bands at the start of any 7d window where `claims_all.n_mentions >= 3` (high-attention windows). Optional V2.

**Data source:** existing `GET /tickers/{ticker}/bars` + `GET /tickers/{ticker}/calls`. No change.

### 4. Recent claims feed (NEW)
**Purpose:** WHAT did creators actually say. Not aggregates — verbatim evidence.
**Content (table, sortable):**
- Date, creator name, `claim_type`, polarity (icon ±), `claim_class` (badge), summary, evidence_quote (collapsible), source link to video timestamp.
- Default sort: most recent first. Default filter: `status='accepted'`, `window=30d`.
- Filters: claim_type multi-select, claim_class multi-select, polarity (bullish/bearish/neutral/mixed), creator filter, time window (7d/30d/90d/all).

**Data source:** `GET /tickers/{ticker}/claims` (NEW endpoint).

### 5. Trade calls table (existing — keep, demote)
**Purpose:** structured calls (entry/target/stop) and their resolved outcomes.
**Content:** existing implementation. Just reorder below the claims feed since claims are higher-volume.

**Data source:** existing `GET /tickers/{ticker}/calls`. No change.

### 6. Creator coverage panel (NEW)
**Purpose:** "who is talking about this ticker, and should I trust them?"
**Content:**
- For each creator who has mentioned this ticker (via call OR claim):
  - Display name + link to creator page.
  - `n_calls + n_claims` on this ticker.
  - Most recent mention date.
  - The creator's most-recent CreatorScorecard.hit_rate_lower_ci (or "uncalibrated" badge if N below threshold).
  - Net polarity of this creator's mentions on this ticker (−1..+1).
- Sort: credibility descending, then mention count descending.
- Empty state: "no tracked creator has mentioned this ticker."

**Data source:** `GET /tickers/{ticker}/coverage` (NEW endpoint).

### 7. Notes & manual review (existing — keep)
**Purpose:** user's own scratchpad (research log).
**Content:** existing `Annotations` component, `Tags` component. No change.

**Data source:** existing endpoints.

### 8. Methodology / data-state footer
**Purpose:** epistemic honesty. Make staleness and sample size visible.
**Content:**
- Last extractor run date for any document on this ticker.
- Last `aggregate-signals` recompute time (from `TickerSignal.computed_at`).
- Sample-size warning: if total mentions < 5, render a "low-N — treat as suggestive, not actionable" banner above the live-signal strip.
- Link to [ADR 0005](decisions/0005-product-pivot-decision-support.md) and [ADR 0006](decisions/0006-claims-and-ticker-signals.md).

**Data source:** existing + `GET /tickers/{ticker}/signals` includes `computed_at`.

## Sections deliberately NOT included (V1)

- **Forecast / "predicted return" / probability of profit.** Per ADR 0005, no prediction output without a calibration plot supporting it.
- **Buy/sell button.** Same reason. The page is decision support; the user pulls the trigger themselves elsewhere.
- **Auto-refresh / live prices.** Polling intervals and SSE belong in a later phase (ADR 0005 §"Things explicitly out of scope").
- **Options chain / volume profile / depth-of-book.** Out of scope — equities only V1.

## Empty / sparse-data behavior

- A ticker with **0 calls and 0 claims** should still render the page (with the chart + a "no extracted signal yet" empty-state). A user might watchlist a ticker before any creator mentions it.
- A ticker with **only old (>90d) mentions** should render with the live-signal strip showing zeros for 7d/30d AND a "historical only — most recent mention N days ago" badge in the header. (Important: prevents the page from looking "live" when it's actually dormant.)

## Required new API endpoints

### `GET /tickers/{ticker}/signals`
- Returns: `TickerSignal[]` for the requested ticker, all (window_size × signal_type) pairs.
- Optional query params: `signal_type` filter, `window_size` filter.
- Latest computation timestamp included for the methodology footer.

### `GET /tickers/{ticker}/claims`
- Returns: `ClaimWithContext[]` (claim row + creator name + document title + source video URL with timestamp).
- Query params: `since` (ISO date), `claim_type[]`, `claim_class[]`, `polarity`, `creator_id`, `status`, `limit`, `offset`.
- Default: `status='accepted'`, sort by `posted_at desc`.

### `GET /tickers/{ticker}/coverage`
- Returns: `CreatorCoverage[]` — per-creator stats for this ticker.
- Each row: `creator_id`, `creator_name`, `n_calls`, `n_claims`, `most_recent_mention_at`, `hit_rate_lower_ci`, `is_calibrated`, `net_polarity_on_this_ticker`.

## Build-order notes

1. **API endpoints first** (this turn): they're frontend-agnostic, fully testable, and unblock both the existing Next.js port and any Huashu-Design mockup work.
2. **Huashu-Design exploration second** (after Claude Code restart picks up the skill): generate 2–3 design directions for the ticker page using the IA above. The skill's "fact-verification first" rule is irrelevant here (we're designing screens, not asserting facts).
3. **Port to Next.js** (third): use the chosen Huashu mockup as the visual spec; reuse existing `Card`, `Annotations`, `Tags`, `WatchlistButton`, `PriceChart` components; add new components for the live-signal strip, claims feed, and creator-coverage panel.
4. **Stop-gap polish** in parallel: the current ticker page is functional but minimal. After the API endpoints land, the existing Next.js page can be incrementally upgraded to use the new data even before final Huashu styling.
