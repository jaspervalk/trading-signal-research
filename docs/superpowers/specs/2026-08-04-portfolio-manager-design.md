# Portfolio Manager — Design Spec

**Date:** 2026-08-04
**Status:** approved (Jasper, 2026-08-04)
**Implements:** [ADR 0009](../../decisions/0009-two-section-restructure.md) section 2 — with one deliberate deviation (see "Deviation from ADR 0009").
**Roadmap slot:** Plan 3 of [the restructure roadmap](../plans/2026-08-04-restructure-roadmap.md), pulled forward ahead of Plan 2 at the owner's request.

## Goal

A manual position/trade ledger that becomes the front door of the application. The owner records what he actually bought and sold (Revolut has no API), refreshes to see live P&L against market data, and jumps from there into research on any ticker.

## Decisions

Locked with the owner on 2026-08-04:

| Question | Decision |
|---|---|
| Currency | Store price per share in the stock's **native** currency, plus an **optional `eur_amount`** — the total EUR that actually moved in the owner's account. No FX-rate modelling for cost basis. |
| Cost basis | **Weighted average.** (The Netherlands taxes wealth via box 3, not realized capital gains, so per-lot accounting buys nothing here.) |
| v1 scope | **Trades + positions only.** No dividends, no cash balance, no deposits/withdrawals. |
| Price freshness | **Intraday quote** (~15-min delayed) fetched on explicit refresh, with a short cache. |

### Deviation from ADR 0009

ADR 0009 sketched three tables: `Position`, `PortfolioTrade`, `CashFlow`. This spec ships **one**: `portfolio_trades`.

- **Positions are derived, never stored.** Storing both trades and positions guarantees drift the first time a trade is edited. Folding trades into positions is a pure function — no database, no network — which is exactly what must be test-covered when it reports the owner's money.
- **`CashFlow` is deferred** with the rest of v1 scope (dividends, cash balance).

ADR 0009 is amended to record this as part of the implementation plan.

## Data model

One new table, `portfolio_trades` — append-and-edit, one row per fill:

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `ticker` | str(16) | uppercased, stripped |
| `side` | str(8) | `buy` \| `sell` |
| `quantity` | float | fractional shares allowed (Revolut supports them) |
| `price_per_share` | float | in `currency`, must be > 0 |
| `currency` | str(3) | native currency of the stock, default `USD` |
| `fees` | float | default 0.0, in `currency`, must be >= 0 |
| `traded_at` | datetime(tz) | UTC tz-aware per project convention; must not be in the future |
| `eur_amount` | float \| None | total EUR that actually left (buy) or entered (sell) the account, **inclusive of fees and FX spread** |
| `note` | str \| None | free text |
| `created_at` / `updated_at` | datetime(tz) | UTC |

Index on `ticker`; index on `traded_at`.

## Ledger math

Trades are folded **in chronological order** (`traded_at`, then `id` as tiebreak). Per ticker the fold carries quantity `Q`, total cost basis `C`, and realized P&L `R`:

```
BUY(q, p, fees):   C += q*p + fees ;  Q += q
SELL(q, p, fees):  avg = C / Q
                   R += (q*p - fees) - q*avg
                   C -= q*avg
                   Q -= q
                   if Q <= EPS: Q = 0.0; C = 0.0
```

`avg_cost = C / Q` when `Q > 0`, else `None`. Buy fees enter cost basis; sell fees reduce proceeds. Average cost is unchanged by a sell — only quantity and total basis shrink. `EPS = 1e-9` guards float dust on a full close.

**Closed positions remain visible** with `quantity = 0` and their realized P&L intact.

### EUR parallel ledger

When **every** trade for a ticker carries `eur_amount`, the identical fold runs a second time over synthetic trades where `price_per_share = eur_amount / quantity` and `fees = 0` (because `eur_amount` is already all-in). This yields `eur_avg_cost` and `eur_realized` with zero extra math — the same pure function, different inputs. If any trade for that ticker lacks `eur_amount`, the EUR view for that position is `None` rather than partially computed.

Current EUR market value of an open position uses the live rate: `EURUSD=X` gives USD per 1 EUR, so `value_eur = value_native / rate` for USD-denominated holdings. EUR-denominated holdings need no conversion.

### Validation

Enforced on create and on edit, by re-folding the full ledger with the candidate change applied:

- Quantity must never go negative at **any point** in the chronological fold. This catches a backdated sell that would have oversold at the time, not merely one that oversells today.
- All trades for a given ticker must share one `currency`; a mismatch is rejected as a data-entry error.
- `quantity > 0`, `price_per_share > 0`, `fees >= 0`, `eur_amount > 0` when present.
- `traded_at` must not be in the future (the project's no-look-ahead discipline).
- `side` ∈ {`buy`, `sell`}.

Rejections return HTTP 422 with a message naming the specific rule.

## Module structure

New package `src/app/portfolio/`, four focused units:

- **`schema.py`** — Pydantic models: `TradeIn`, `TradeOut`, `Position`, `PortfolioView`, `Quote`.
- **`ledger.py`** — pure functions. `fold_trades(trades) -> list[Position]`, `validate_ledger(trades)`. No DB, no network, no clock beyond what is passed in.
- **`pricing.py`** — `get_quotes(tickers) -> dict[str, Quote | None]` via yfinance `fast_info` (last price, previous close, currency), plus `get_eur_rate()`. In-process TTL cache (60s) so repeated refreshes don't hammer yfinance. Per-ticker failure yields `None`, never an exception.
- **`service.py`** — reads trades from the DB, folds them, applies quotes, assembles `PortfolioView` with totals.

## API surface

New router `apps/api/app/routes/portfolio.py`:

```
GET    /portfolio                  # PortfolioView: positions + totals + as_of + fx rate
GET    /portfolio/trades?ticker=   # trade ledger, newest first
POST   /portfolio/trades           # create  -> 201
PATCH  /portfolio/trades/{id}      # edit
DELETE /portfolio/trades/{id}      # delete  -> 204
```

No position endpoints — positions are derived and only ever read through `GET /portfolio`.

## CLI surface

```bash
tsr pf add NVDA --side buy --qty 10 --price 145.20 --date 2026-07-15 [--fees 1.50] [--eur 1320.50] [--note "..."]
tsr pf list [--include-closed]     # positions with live P&L
tsr pf trades [--ticker NVDA]      # raw ledger
tsr pf rm <trade_id>               # delete a mistaken entry
```

## Web IA

**`/` becomes the portfolio.** Contents, top to bottom:

1. **Ticker search box** — type a symbol, land on `/tickers/<TICKER>` (the existing research page). This is the "look into a trade" entry point.
2. **Totals bar** — total market value, total unrealized P&L (native + EUR), total realized P&L, `as_of` timestamp, refresh button.
3. **Holdings table** — one row per open position: ticker, quantity, average cost, last price, market value, unrealized P&L (absolute + %), day change. Each ticker links to its research page. Price-unavailable rows render "—" and stay visible.
4. **Closed positions** — collapsed section, realized P&L per ticker.
5. **Add trade form** — ticker, side, quantity, price, date, optional fees / EUR total / note.
6. **Trade ledger** — recent trades with inline edit and delete.

Refresh is explicit; nothing auto-polls.

**Navigation** becomes: **Portfolio** (`/`) · **Research** (`/tickers`) · **Watchlist** · **Creators** (`/creators`) · **Methodology**. The creator leaderboard currently at `/` moves to a new `/creators` index page. The gold-set pages leave the top nav (extractor tooling, not a product surface) but remain reachable by URL.

## Out of scope (v1)

Dividends; cash balance, deposits and withdrawals; FIFO lot tracking; broker/CSV import; multi-account or multi-portfolio support; realized-P&L tax reporting; **position-sizing advice and any order execution** (forbidden outright by ADR 0005 §4, ADR 0008 §4 and ADR 0009 — the system records what is held and never says how much to buy).

The trade ledger is shaped so dividends and cash flows can be added later as sibling tables without reworking `portfolio_trades` or the fold.

## Testing

**`ledger.py` — real unit tests, no mocks** (this module reports the owner's money):

- single buy produces the expected position
- multiple buys produce a correctly weighted average cost
- partial sell realizes the right amount and leaves average cost unchanged
- full close leaves `quantity == 0` with realized P&L intact and the position still visible
- oversell is rejected
- a backdated sell that would have oversold *at the time* is rejected even if today's quantity would allow it
- buy fees raise cost basis; sell fees reduce proceeds
- multiple tickers stay isolated
- the EUR parallel ledger matches the native fold's shape; a single missing `eur_amount` yields `None` for that position rather than a partial number

**`pricing.py`** — against a fake quote source: cache hit/miss behaviour, per-ticker failure yields `None` without raising, EUR rate conversion arithmetic.

**API** — create/edit/delete round-trip, each validation rule returning 422, and `GET /portfolio` assembling positions with mocked quotes.

## Migration

One Alembic migration creating `portfolio_trades`. No existing data is touched; nothing to backfill.
