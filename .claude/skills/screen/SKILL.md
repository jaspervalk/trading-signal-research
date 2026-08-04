---
name: screen
description: Run the stock screener funnel over the S&P 500 + Nasdaq-100 universe and present candidate tickers with metrics + filters-passed. Use when the user wants to "find candidates", "screen for value/growth/quality/momentum", "find undervalued stocks", "find new tickers", or anything that surfaces names outside the YouTube/Twitter universe.
allowed-tools: Read, Bash(tsr screen*), Bash(tsr screen-refresh-universe*), Glob, Grep
---

# /screen

Run the stock screener and present a short summary of candidates worth researching next via `tsr research <TICKER>` (deterministic view) or Deep mode in the dashboard (`POST /research/deep/{ticker}`).

The screener is a **funnel, not a ranking model** (ADR 0005 framing applies). It filters the S&P 500 + Nasdaq-100 down to ~20-40 candidates based on independent valuation / growth / quality / technical filters. No composite scores. Creator coverage is shown as a bonus column but **never** used to rank or filter — finding tickers our YouTube universe has not discovered yet is the entire point.

## Procedure

1. **Confirm scope** in one line. Default is "all filters, full universe, table output." If the user mentions a sector, a specific filter, or a threshold override, use it.
2. **Run `tsr screen`** with appropriate flags. The most common shapes:
   - `tsr screen` — all four filters, full universe.
   - `tsr screen --value --quality` — pure value + quality cross.
   - `tsr screen --sector Technology` — restrict universe by sector.
   - `tsr screen --min-pe 20 --max-peg 1.2` — tighter thresholds.
   - `tsr screen --output json` for machine-readable output.
3. **Summarise the results to the user** in a tight format:
   - Count of tickers that passed at least one filter.
   - Which filters were most restrictive (counts per filter).
   - Top 5-10 candidates with: ticker, sector, filters passed, any flags (`cheap_for_a_reason`, `earnings_in_Xd`, `extended_above_200d`).
   - Suggested next steps — typically: run `tsr research <TICKER>` on the top 3-5, then Deep mode from the dashboard for the strongest 1-2.
4. **Surface flags accurately.** `cheap_for_a_reason` means valuation looks low but earnings are negative — that's not automatically bad, but it changes the research question. `earnings_in_Xd` is informational, not a filter.

## Hard rules

- **Creator coverage is additive, never penalising.** When summarising, do not rank tickers higher because they have YouTube coverage, and do not rank tickers lower because they don't. The point of the screen is to find tickers the YouTube universe hasn't discovered. The `creator_mentions` column is shown for context only.
- **No composite scores.** The screener does not emit a single number; do not invent one when summarising.
- **The user pulls the trigger.** Outputs are decision support — name the candidates worth researching, never assert "buy this."

## When data is stale

If the universe file at `configs/screen_universe.csv` was last refreshed >90 days ago, mention this and suggest `tsr screen-refresh-universe` (which fetches current S&P 500 + Nasdaq-100 from Wikipedia and rewrites the CSV).

## See also

- ADR 0005 — product pivot to decision-support: [docs/decisions/0005-product-pivot-decision-support.md](../../../docs/decisions/0005-product-pivot-decision-support.md)
- Module: [src/app/screener/](../../../src/app/screener/)
- Universe: [configs/screen_universe.csv](../../../configs/screen_universe.csv)
