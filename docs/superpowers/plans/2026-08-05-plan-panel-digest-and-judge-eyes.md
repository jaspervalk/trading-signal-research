# Panel Digest + Judge Valuation Awareness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the defect where the Deep-research judge writes the plan without ever seeing valuation, and make the panel data safe to serialize to a model (real cache TTLs, freshness stamps, explicit unit tags, enforced output limits).

**Architecture:** Steps 1 and 2 of [docs/design/thematic-agent.md](../../design/thematic-agent.md). Four deterministic changes: a TTL cache replacing two unbounded `lru_cache`s; a `fetched_at` stamp on `ValuationPanel`; a new `PanelDigest` that flattens panels into self-describing `Measure` rows with explicit units; structural length limits on the LLM tool schemas; and finally passing a valuation block to the judge. **The thematic lens (Step 3) is deliberately NOT part of this plan.**

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy 2.0, Anthropic SDK, pytest.

## Global Constraints

- Tests run with `.venv/bin/python -m pytest` — bare `python` is NOT on PATH.
- Baseline before this plan: **533 tests passing**. Every task must leave the full suite green.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Work on `main`. Never commit `data/`, `CLAUDE.md`, or `.env`.
- All datetimes UTC tz-aware (`datetime.now(timezone.utc)` or `datetime.now(tz=UTC)` matching the file's existing import style); never naive.
- **The index-picking guard is load-bearing and must not be weakened**: the judge emits `entry_kind` plus integer indices, never prices. No task here changes that.
- **No new network calls.** No test may hit the network; yfinance access is monkeypatched.
- Do not change `EntryExitPlan`'s field *shapes* — the Next.js client at `apps/web/src/lib/api.ts` mirrors them. Adding optional fields is fine; changing a `list[str]` to a list of objects is not.
- Units vocabulary is fixed: `ratio | fraction | percent | usd | shares | days | index_0_100 | price | categorical`.

---

### Task 1: TTL cache utility

**Files:**
- Create: `src/app/ttl_cache.py`
- Test: `tests/test_ttl_cache.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ttl_cache(seconds, maxsize=256)` decorator. The wrapped function gains `.cache_clear()` and `.peek_fetched_at(*args, **kwargs) -> datetime | None`. Task 2 consumes both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ttl_cache.py`:

```python
"""TTL cache: expiry, fetch-time introspection, and clearing."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ttl_cache import ttl_cache


def test_caches_within_ttl():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x * 2

    assert f(3) == 6
    assert f(3) == 6
    assert calls == [3]


def test_expires_after_ttl(monkeypatch):
    calls = []
    clock = [1000.0]

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x * 2

    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])
    f(3)
    clock[0] += 101
    f(3)
    assert calls == [3, 3]


def test_distinct_args_cached_separately():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x

    f("a")
    f("b")
    f("a")
    assert calls == ["a", "b"]


def test_peek_fetched_at_returns_utc_aware_time():
    @ttl_cache(seconds=100)
    def f(x):
        return x

    assert f.peek_fetched_at("NVDA") is None
    f("NVDA")
    stamp = f.peek_fetched_at("NVDA")
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is not None
    assert stamp.tzinfo.utcoffset(stamp) == UTC.utcoffset(stamp)


def test_peek_fetched_at_is_none_after_expiry(monkeypatch):
    clock = [1000.0]

    @ttl_cache(seconds=10)
    def f(x):
        return x

    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])
    f("NVDA")
    assert f.peek_fetched_at("NVDA") is not None
    clock[0] += 11
    assert f.peek_fetched_at("NVDA") is None


def test_cache_clear_drops_everything():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x

    f(1)
    f.cache_clear()
    f(1)
    assert calls == [1, 1]
    assert f.peek_fetched_at(1) is not None


def test_maxsize_evicts_oldest():
    @ttl_cache(seconds=100, maxsize=2)
    def f(x):
        return x

    f(1)
    f(2)
    f(3)
    assert f.peek_fetched_at(1) is None
    assert f.peek_fetched_at(3) is not None


def test_exceptions_are_not_cached():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        raise ValueError("boom")

    for _ in range(2):
        try:
            f(1)
        except ValueError:
            pass
    assert calls == [1, 1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ttl_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ttl_cache'`.

- [ ] **Step 3: Write `src/app/ttl_cache.py`**

```python
"""A small time-to-live cache decorator.

`functools.lru_cache` never expires, which is wrong for market metadata:
a forward P/E derived from a live price goes stale within minutes, and an
unbounded cache in a long-lived FastAPI worker will serve the same blob
forever. This decorator adds expiry plus one thing `lru_cache` cannot give
us — `peek_fetched_at`, so a caller can stamp its output with the age of
the data it was built from.

Not thread-safe by design: the worst case under a race is a duplicate
upstream fetch, which is what the un-cached path did anyway.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

__all__ = ["ttl_cache"]


def _monotonic() -> float:
    """Indirection so tests can freeze the clock."""
    return time.monotonic()


def _make_key(args: tuple, kwargs: dict) -> tuple:
    if not kwargs:
        return args
    return args + tuple(sorted(kwargs.items()))


def ttl_cache(seconds: float, maxsize: int = 256) -> Callable:
    """Memoise a function for `seconds`, evicting oldest beyond `maxsize`.

    Exceptions are never cached. The wrapper exposes `cache_clear()` and
    `peek_fetched_at(*args, **kwargs)`, which returns the UTC time the
    cached value was computed, or None when there is no live entry.
    """

    def decorator(fn: Callable) -> Callable:
        # key -> (stored_at_monotonic, fetched_at_utc, value)
        store: OrderedDict[tuple, tuple[float, datetime, Any]] = OrderedDict()

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _make_key(args, kwargs)
            now = _monotonic()
            hit = store.get(key)
            if hit is not None and now - hit[0] < seconds:
                store.move_to_end(key)
                return hit[2]

            value = fn(*args, **kwargs)  # exceptions propagate, uncached
            store[key] = (now, datetime.now(tz=UTC), value)
            store.move_to_end(key)
            while len(store) > maxsize:
                store.popitem(last=False)
            return value

        def cache_clear() -> None:
            store.clear()

        def peek_fetched_at(*args: Any, **kwargs: Any) -> datetime | None:
            hit = store.get(_make_key(args, kwargs))
            if hit is None or _monotonic() - hit[0] >= seconds:
                return None
            return hit[1]

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.peek_fetched_at = peek_fetched_at  # type: ignore[attr-defined]
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ttl_cache.py -q`
Expected: 8 passed.

- [ ] **Step 5: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 541 passed.

```bash
git add src/app/ttl_cache.py tests/test_ttl_cache.py
git commit -m "feat(cache): TTL cache decorator with fetch-time introspection

lru_cache never expires, which is wrong for price-derived metadata. Adds
expiry plus peek_fetched_at so callers can stamp output with data age.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Valuation freshness stamp + real TTL

**Files:**
- Modify: `src/app/analysis/schema.py` (add `fetched_at` to `ValuationPanel`)
- Modify: `src/app/analysis/research.py` (swap two `lru_cache`s for `ttl_cache`, stamp the panel)
- Test: `tests/test_valuation_freshness.py`

**Interfaces:**
- Consumes: Task 1's `ttl_cache`.
- Produces: `ValuationPanel.fetched_at: datetime | None`; module constants `METADATA_TTL_SECONDS = 900` and `EARNINGS_TTL_SECONDS = 21600` in `research.py`. Task 3 consumes `fetched_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_valuation_freshness.py`:

```python
"""ValuationPanel carries the age of the metadata it was built from."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis import research as research_mod
from app.analysis.schema import ValuationPanel

META = {
    "marketCap": 1.0e12,
    "forwardPE": 30.0,
    "revenueGrowth": 0.25,
    "dividendYield": 0.38,
    "sector": "Technology",
    "industry": "Semiconductors",
}


def test_valuation_panel_has_fetched_at_field():
    assert "fetched_at" in ValuationPanel.model_fields
    assert ValuationPanel().fetched_at is None


def test_build_valuation_stamps_fetched_at(monkeypatch):
    research_mod._resolve_metadata.cache_clear()
    monkeypatch.setattr(research_mod, "_yf_info", lambda t: dict(META))
    research_mod._resolve_metadata("NVDA")

    panel = research_mod._build_valuation(
        ticker="NVDA",
        metadata=dict(META),
        as_of=datetime.now(tz=UTC),
        fetch_metadata=False,
    )
    assert panel.fetched_at is not None
    assert panel.fetched_at.tzinfo is not None
    assert panel.forward_pe == 30.0


def test_empty_metadata_yields_unstamped_panel():
    panel = research_mod._build_valuation(
        ticker="NOSUCH",
        metadata={},
        as_of=datetime.now(tz=UTC),
        fetch_metadata=False,
    )
    assert panel.fetched_at is None
    assert panel.forward_pe is None


def test_metadata_cache_expires(monkeypatch):
    research_mod._resolve_metadata.cache_clear()
    calls = []
    clock = [1000.0]

    def fake_info(ticker):
        calls.append(ticker)
        return dict(META)

    monkeypatch.setattr(research_mod, "_yf_info", fake_info)
    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])

    research_mod._resolve_metadata("NVDA")
    research_mod._resolve_metadata("NVDA")
    assert calls == ["NVDA"]

    clock[0] += research_mod.METADATA_TTL_SECONDS + 1
    research_mod._resolve_metadata("NVDA")
    assert calls == ["NVDA", "NVDA"]


def test_metadata_fetch_failure_returns_empty_dict(monkeypatch):
    research_mod._resolve_metadata.cache_clear()

    def boom(ticker):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(research_mod, "_yf_info", boom)
    assert research_mod._resolve_metadata("NVDA") == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_valuation_freshness.py -q`
Expected: FAIL — no `fetched_at` field, and `_yf_info` does not exist.

- [ ] **Step 3: Add the field**

In `src/app/analysis/schema.py`, inside `ValuationPanel`, after the `industry` field (currently line ~172), add:

```python
    # Age of the yfinance metadata this panel was built from. None when
    # metadata was unavailable. Consumers (and any LLM contract) must be
    # able to tell a 10-minute-old multiple from a 10-day-old one.
    fetched_at: datetime | None = None
```

- [ ] **Step 4: Swap the caches and stamp the panel**

In `src/app/analysis/research.py`:

Add the import near the other `app.` imports:
```python
from app.ttl_cache import ttl_cache
```

Add the constants just above `_resolve_metadata` (replacing the `# yfinance metadata` comment block's trailing blank line):
```python
# Metadata TTL. forward_pe and friends are price-derived, so they go stale
# within minutes; the earnings date does not, so it gets a longer window.
METADATA_TTL_SECONDS = 900  # 15 minutes
EARNINGS_TTL_SECONDS = 21600  # 6 hours
```

Extract the raw fetch so tests can patch it without touching the cache layer, and swap the decorator:
```python
def _yf_info(ticker: str) -> dict:
    """Raw yfinance .info fetch. Split out so tests can patch it."""
    import yfinance as yf

    info = yf.Ticker(ticker).get_info()
    return dict(info) if info else {}


@ttl_cache(seconds=METADATA_TTL_SECONDS)
def _resolve_metadata(ticker: str) -> dict:
    """Best-effort wrapper around yf.Ticker(...).info.

    Cached for METADATA_TTL_SECONDS. Failures are silent; the research view
    degrades gracefully when metadata is missing. Do not call on a hot path.
    """
    try:
        return _yf_info(ticker)
    except Exception as e:  # pragma: no cover — yfinance is flaky
        log.warning("research.metadata.error", ticker=ticker, error=str(e))
        return {}
```

Change `_resolve_next_earnings`'s decorator from `@lru_cache(maxsize=256)` to:
```python
@ttl_cache(seconds=EARNINGS_TTL_SECONDS)
```
Leave its body unchanged.

Add a keyword-only `fetched_at: datetime | None = None` parameter to `_build_valuation`, and pass it straight through as the final keyword argument of the `ValuationPanel(...)` construction (after `industry=...`):
```python
        fetched_at=fetched_at,
```

At the one production call site in `build_ticker_research_view`, capture the stamp on the line immediately after resolving the metadata, so the two cannot drift apart:
```python
    metadata_fetched_at = _resolve_metadata.peek_fetched_at(ticker) if fetch_metadata else None
```
and pass it into `_build_valuation(...)`. **Do not** call `peek_fetched_at` inside `_build_valuation` — the stamp must describe the `metadata` dict that was actually passed in, not whatever happens to be in the ticker-keyed cache. Task 3 hands this stamp to the judge as a freshness signal, so a mismatch becomes a confidently-wrong claim about data age.

Leave `_load_universe`'s `@lru_cache(maxsize=1)` alone — a config file read once per process is correct. If `lru_cache` becomes an unused import after this change, remove it; if `_load_universe` still uses it, keep it.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_valuation_freshness.py -q`
Expected: 5 passed.

- [ ] **Step 6: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 546 passed. If any pre-existing test patched `_resolve_metadata` via `lru_cache`-specific behaviour, fix that test to use `cache_clear()` (still available) and note it in the report.

```bash
git add src/app/analysis/schema.py src/app/analysis/research.py tests/test_valuation_freshness.py
git commit -m "feat(analysis): stamp ValuationPanel with fetched_at; give metadata a real TTL

_resolve_metadata and _resolve_next_earnings were unbounded lru_caches with
no expiry and no cache_clear call in production — a long-lived worker served
the same forward P/E forever. Now 15min / 6h TTLs, and the panel carries the
age of the data it was built from.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: PanelDigest

**Files:**
- Create: `src/app/analysis/digest.py`
- Test: `tests/test_panel_digest.py`

**Interfaces:**
- Consumes: `TickerResearchView`, `FundamentalsExtended`, `PeerComparison`, Task 2's `fetched_at`.
- Produces: `Unit` (Literal alias), `Measure`, `PanelDigest`, `build_panel_digest(view, *, fundamentals=None, peers=None) -> PanelDigest`, and `PanelDigest.valuation_block() -> str`. Task 5 consumes `valuation_block()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_panel_digest.py`:

```python
"""PanelDigest: flat, unit-tagged, citable panel measures."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.digest import Measure, PanelDigest, build_panel_digest
from app.analysis.schema import (
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    TickerResearchView,
    ValuationPanel,
)

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _view(**overrides):
    kwargs = dict(
        ticker="NVDA",
        as_of=AS_OF,
        market=MarketSnapshotPanel(as_of=AS_OF, last_close=100.0, return_21d=0.12),
        valuation=ValuationPanel(
            forward_pe=30.0,
            revenue_growth_yoy=0.25,
            dividend_yield=0.38,
            market_cap=1.0e12,
            sector="Technology",
            fetched_at=AS_OF,
        ),
        indicators=IndicatorPanel(rsi_14=71.0, atr_14=3.5),
        levels=LevelsPanel(nearest_support=95.0),
    )
    kwargs.update(overrides)
    return TickerResearchView(**kwargs)


def test_digest_is_flat_and_citable():
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert "valuation.forward_pe" in by_field
    assert by_field["valuation.forward_pe"].value == 30.0
    assert d.ticker == "NVDA"


def test_dividend_yield_is_tagged_percent_not_fraction():
    """yfinance pre-multiplies dividendYield; every other rate is a fraction.
    A model told 'fraction' would render 0.38% as 38%."""
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.dividend_yield"].unit == "percent"
    assert by_field["valuation.revenue_growth_yoy"].unit == "fraction"


def test_units_are_assigned_per_field():
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].unit == "ratio"
    assert by_field["valuation.market_cap"].unit == "usd"
    assert by_field["indicators.rsi_14"].unit == "index_0_100"
    assert by_field["indicators.atr_14"].unit == "price"
    assert by_field["market.return_21d"].unit == "fraction"
    assert by_field["valuation.sector"].unit == "categorical"


def test_missing_fields_are_named_not_silently_dropped():
    d = build_panel_digest(_view())
    assert "valuation.trailing_pe" in d.missing
    assert all(m.field != "valuation.trailing_pe" for m in d.measures)


def test_every_measure_carries_a_timezone_aware_as_of():
    d = build_panel_digest(_view())
    assert d.measures
    for m in d.measures:
        assert m.as_of.tzinfo is not None


def test_valuation_measures_inherit_panel_fetched_at():
    stamp = datetime(2026, 8, 1, tzinfo=UTC)
    d = build_panel_digest(_view(valuation=ValuationPanel(forward_pe=30.0, fetched_at=stamp)))
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].as_of == stamp


def test_valuation_without_fetched_at_falls_back_to_view_as_of():
    d = build_panel_digest(_view(valuation=ValuationPanel(forward_pe=30.0)))
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].as_of == AS_OF


def test_digest_handles_a_fully_empty_view():
    view = TickerResearchView(ticker="EMPTY", as_of=AS_OF, market=MarketSnapshotPanel(as_of=AS_OF))
    d = build_panel_digest(view)
    assert d.measures == [] or all(m.value is not None for m in d.measures)
    assert "valuation.forward_pe" in d.missing


def test_valuation_block_renders_only_present_fields():
    block = build_panel_digest(_view()).valuation_block()
    assert "forward_pe" in block
    assert "30.0" in block
    assert "trailing_pe" not in block


def test_valuation_block_states_units_and_age():
    block = build_panel_digest(_view()).valuation_block()
    assert "percent" in block or "%" in block
    assert "as of" in block.lower() or "fetched" in block.lower()


def test_valuation_block_is_explicit_when_nothing_is_available():
    view = TickerResearchView(ticker="EMPTY", as_of=AS_OF, market=MarketSnapshotPanel(as_of=AS_OF))
    block = build_panel_digest(view).valuation_block()
    assert "no valuation data" in block.lower()


def test_measure_rejects_an_unknown_unit():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Measure(field="x.y", value=1.0, unit="furlongs", as_of=AS_OF)


def test_digest_is_json_serialisable():
    d = build_panel_digest(_view())
    assert PanelDigest.model_validate_json(d.model_dump_json()).ticker == "NVDA"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_panel_digest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.analysis.digest'`.

- [ ] **Step 3: Write `src/app/analysis/digest.py`**

```python
"""Flatten the ticker panels into self-describing, citable measures.

Three unit conventions coexist in this codebase and none of them are marked
on the models: most rates are fractions (0.25 = 25%), `dividend_yield` alone
arrives from yfinance already multiplied (0.38 = 0.38%), and prices, market
caps and share counts are absolutes. `rsi_14` is a 0-100 index. Anything
serialising these to a consumer that cannot see the source — an LLM, an
export, another service — will misread them by 100x sooner or later.

A `PanelDigest` is a flat list of `Measure` rows. Each carries its dotted
field path (which doubles as a citation key), its unit, and the age of the
data it came from. Fields that are None are listed in `missing` rather than
dropped, so absence is visible rather than inferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.analysis.schema import TickerResearchView

Unit = Literal[
    "ratio",         # a pure multiple, e.g. forward P/E
    "fraction",      # 0.25 == 25%
    "percent",       # 0.38 == 0.38% (already multiplied upstream)
    "usd",           # absolute currency
    "shares",        # absolute share count
    "days",          # whole days, may be negative
    "index_0_100",   # bounded oscillator, e.g. RSI
    "price",         # a price level in the instrument's currency
    "categorical",   # a string label
]

# (dotted path, unit). Order here is the order in the digest.
_VALUATION_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("market_cap", "usd"),
    ("forward_pe", "ratio"),
    ("trailing_pe", "ratio"),
    ("peg_ratio", "ratio"),
    ("price_to_sales_ttm", "ratio"),
    ("price_to_book", "ratio"),
    ("enterprise_to_ebitda", "ratio"),
    ("earnings_growth_forward", "fraction"),
    ("revenue_growth_yoy", "fraction"),
    ("profit_margins", "fraction"),
    ("float_shares", "shares"),
    ("shares_outstanding", "shares"),
    ("short_pct_of_float", "fraction"),
    ("held_pct_institutions", "fraction"),
    ("beta", "ratio"),
    # yfinance returns dividendYield pre-multiplied. This is the one field
    # in the panel that is NOT a fraction; mislabelling it is a 100x error.
    ("dividend_yield", "percent"),
    ("days_to_next_earnings", "days"),
    ("sector", "categorical"),
    ("industry", "categorical"),
)

_MARKET_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("last_close", "price"),
    ("return_5d", "fraction"),
    ("return_21d", "fraction"),
    ("return_63d", "fraction"),
    ("return_252d", "fraction"),
    ("excess_return_21d", "fraction"),
    ("pct_off_52w_high", "fraction"),
    ("pct_off_52w_low", "fraction"),
    ("dollar_volume", "usd"),
)

_INDICATOR_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("rsi_14", "index_0_100"),
    ("atr_14", "price"),
    ("atr_14_pct", "fraction"),
    ("ma_alignment", "categorical"),
    ("sma_50_slope_21d_pct", "fraction"),
    ("sma_200_slope_63d_pct", "fraction"),
    ("dist_to_sma_50_pct", "fraction"),
    ("dist_to_sma_200_pct", "fraction"),
    ("realized_vol_21d_annualized", "fraction"),
    ("relative_strength_vs_spy_63d", "fraction"),
)

_LEVELS_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("nearest_support", "price"),
    ("nearest_resistance", "price"),
    ("recent_high_63d", "price"),
    ("base_low", "price"),
    ("pullback_pct_from_recent_high", "fraction"),
    ("breakout_distance_pct", "fraction"),
)

# Materialised @property reads off FundamentalsExtended / PeerComparison.
# These are frozen dataclasses whose most useful fields are properties, so
# they vanish under dataclasses.asdict() and must be named explicitly.
_FUNDAMENTALS_PROPERTIES: tuple[tuple[str, Unit], ...] = (
    ("operating_margin_trajectory", "categorical"),
    ("balance_sheet_strength", "categorical"),
    ("capital_allocation", "categorical"),
)
_FUNDAMENTALS_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("fcf_yield", "fraction"),
    ("debt_to_equity", "ratio"),
    ("current_ratio", "ratio"),
    ("cash_to_market_cap", "fraction"),
    ("shares_outstanding_yoy_pct", "fraction"),
)
_PEER_PROPERTIES: tuple[tuple[str, Unit], ...] = (
    ("forward_pe_relative", "categorical"),
    ("growth_relative", "categorical"),
    ("margin_relative", "categorical"),
)
_PEER_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("median_forward_pe", "ratio"),
    ("median_peg", "ratio"),
    ("median_revenue_growth_yoy", "fraction"),
    ("median_profit_margins", "fraction"),
)


class Measure(BaseModel):
    """One panel field, self-describing.

    `field` is the dotted path and doubles as a citation key: a consumer
    quoting a number is expected to name the field it came from, and that
    name can be checked against the digest.
    """

    field: str
    value: float | int | str | None
    unit: Unit
    as_of: datetime


class PanelDigest(BaseModel):
    """Every panel number for one ticker, flat and unit-tagged."""

    ticker: str
    as_of: datetime
    measures: list[Measure] = []
    missing: list[str] = []

    def by_field(self) -> dict[str, Measure]:
        return {m.field: m for m in self.measures}

    def has(self, field: str) -> bool:
        return any(m.field == field for m in self.measures)

    def valuation_block(self) -> str:
        """Render the valuation measures as prompt-ready text.

        Only present fields are rendered; each line states the unit so the
        reader cannot mistake a fraction for a percent. Absent fields are
        summarised on one line so the reader knows what was unavailable
        rather than assuming it was zero.
        """
        rows = [m for m in self.measures if m.field.startswith("valuation.")]
        if not rows:
            return "(no valuation data available for this ticker)"

        lines: list[str] = []
        for m in rows:
            name = m.field.split(".", 1)[1]
            if isinstance(m.value, float):
                rendered = f"{m.value:,.4g}"
            else:
                rendered = str(m.value)
            lines.append(f"- {name}: {rendered} ({m.unit})")

        stamp = max(m.as_of for m in rows)
        lines.append(f"(valuation as of {stamp.isoformat()})")

        absent = [f.split(".", 1)[1] for f in self.missing if f.startswith("valuation.")]
        if absent:
            lines.append(f"(unavailable: {', '.join(absent)})")
        return "\n".join(lines)


def _collect(
    source: object | None,
    prefix: str,
    spec: tuple[tuple[str, Unit], ...],
    as_of: datetime,
    measures: list[Measure],
    missing: list[str],
) -> None:
    for name, unit in spec:
        path = f"{prefix}.{name}"
        value = getattr(source, name, None) if source is not None else None
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(path)
            continue
        measures.append(Measure(field=path, value=value, unit=unit, as_of=as_of))


def build_panel_digest(
    view: TickerResearchView,
    *,
    fundamentals: object | None = None,
    peers: object | None = None,
) -> PanelDigest:
    """Flatten `view` (plus optional Deep-mode extras) into a `PanelDigest`.

    `fundamentals` and `peers` are `FundamentalsExtended` / `PeerComparison`
    when available — typed loosely to avoid importing market modules into the
    analysis layer.
    """
    measures: list[Measure] = []
    missing: list[str] = []
    as_of = view.as_of

    # Valuation measures carry the metadata's own age when it is known.
    valuation_as_of = getattr(view.valuation, "fetched_at", None) or as_of

    _collect(view.valuation, "valuation", _VALUATION_FIELDS, valuation_as_of, measures, missing)
    _collect(view.market, "market", _MARKET_FIELDS, as_of, measures, missing)
    _collect(view.indicators, "indicators", _INDICATOR_FIELDS, as_of, measures, missing)
    _collect(view.levels, "levels", _LEVELS_FIELDS, as_of, measures, missing)
    _collect(fundamentals, "fundamentals", _FUNDAMENTALS_FIELDS, as_of, measures, missing)
    _collect(fundamentals, "fundamentals", _FUNDAMENTALS_PROPERTIES, as_of, measures, missing)
    _collect(peers, "peers", _PEER_FIELDS, as_of, measures, missing)
    _collect(peers, "peers", _PEER_PROPERTIES, as_of, measures, missing)

    return PanelDigest(
        ticker=view.ticker,
        as_of=as_of,
        measures=measures,
        missing=missing,
    )


__all__ = ["Measure", "PanelDigest", "Unit", "build_panel_digest"]
```

Note: the `@property` reads on `FundamentalsExtended` (`operating_margin_trajectory` etc.) return the string `"unknown"` rather than `None` when data is absent. That is a real value and will appear as a measure — acceptable, since "unknown" is itself information the reader should see.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_panel_digest.py -q`
Expected: 13 passed.

- [ ] **Step 5: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 559 passed.

```bash
git add src/app/analysis/digest.py tests/test_panel_digest.py
git commit -m "feat(analysis): PanelDigest — flat, unit-tagged, citable panel measures

Three unit conventions coexist unmarked on the panels (fraction, the
pre-multiplied dividend_yield, and absolutes). The digest tags each field,
stamps it with the age of its source, and names missing fields instead of
dropping them.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Structural output limits

**Files:**
- Modify: `src/app/research/agents/base.py` (lens tool schema)
- Modify: `src/app/research/agents/judge.py` (synthesis tool schema)
- Modify: `src/app/research/quick.py` (quick tool schema)
- Modify: `src/app/research/schema.py` (clamping validators)
- Test: `tests/test_research_output_limits.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MAX_SUMMARY_CHARS = 240`, `MAX_POINT_CHARS = 400`, `MAX_CASE_ITEMS = 5` in `research/schema.py`, plus clamping validators on `EntryExitPlan` and `LensView`.

Every length constraint in this feature currently lives in prose only — "≤25 words", "3-5 bullets" — while the judge's tool schema permits 8 items. The API does not strictly enforce tool schemas, so the schema tightening is advisory and the Python clamp is the real guard. **Clamp, never reject**: discarding a whole plan because one bullet ran long would be worse than truncating it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_output_limits.py`:

```python
"""Output limits are enforced in Python, not merely requested in prose."""

from __future__ import annotations

from datetime import UTC, datetime

from app.research.schema import (
    MAX_CASE_ITEMS,
    MAX_POINT_CHARS,
    MAX_SUMMARY_CHARS,
    EntryExitPlan,
    LensView,
    ZoneBand,
)

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _plan(**overrides):
    kwargs = dict(
        ticker="NVDA",
        as_of=AS_OF,
        entry_zone=ZoneBand(low=100.0, high=102.0, method="m"),
        exit_zone_primary=ZoneBand(low=110.0, high=112.0, method="m"),
        invalidation=95.0,
        risk_reward_primary=2.0,
        confidence="medium",
        timeframe="5-15d",
        mode="deep",
        cost_usd=0.05,
        duration_ms=1000,
    )
    kwargs.update(overrides)
    return EntryExitPlan(**kwargs)


def test_case_lists_are_clamped_to_max_items():
    plan = _plan(bull_case=[f"point {i}" for i in range(12)])
    assert len(plan.bull_case) == MAX_CASE_ITEMS


def test_bear_case_and_key_risks_are_clamped_too():
    plan = _plan(
        bear_case=[f"b{i}" for i in range(12)],
        key_risks=[f"r{i}" for i in range(12)],
    )
    assert len(plan.bear_case) == MAX_CASE_ITEMS
    assert len(plan.key_risks) == MAX_CASE_ITEMS


def test_overlong_case_entry_is_truncated_not_rejected():
    plan = _plan(bull_case=["x" * (MAX_POINT_CHARS + 500)])
    assert len(plan.bull_case[0]) <= MAX_POINT_CHARS
    assert plan.bull_case[0].endswith("…")


def test_short_entries_are_left_untouched():
    plan = _plan(bull_case=["a tidy point"])
    assert plan.bull_case == ["a tidy point"]


def test_lens_summary_is_truncated():
    lens = LensView(name="quantitative", conviction="high", summary="y" * 1000)
    assert len(lens.summary) <= MAX_SUMMARY_CHARS


def test_lens_points_are_clamped_and_truncated():
    lens = LensView(
        name="quantitative",
        conviction="high",
        points=[f"p{i}" for i in range(20)] + ["z" * 900],
    )
    assert len(lens.points) <= MAX_CASE_ITEMS
    assert all(len(p) <= MAX_POINT_CHARS for p in lens.points)


def test_bare_string_wrapping_still_works():
    """Regression guard for the 2026-05-28 char-splitting incident."""
    plan = _plan(bull_case="a single bare string")
    assert plan.bull_case == ["a single bare string"]


def test_judge_tool_schema_matches_the_prompt_and_bounds_strings():
    from app.research.agents.judge import judge_tool_input_schema

    props = judge_tool_input_schema()["properties"]
    assert props["bull_case"]["maxItems"] == MAX_CASE_ITEMS
    assert props["bear_case"]["maxItems"] == MAX_CASE_ITEMS
    assert props["key_risks"]["maxItems"] == MAX_CASE_ITEMS
    assert props["entry_rationale"]["maxLength"] == MAX_POINT_CHARS


def test_lens_tool_schema_bounds_summary_and_points():
    from app.research.agents.base import _lens_tool_input_schema

    props = _lens_tool_input_schema()["properties"]
    assert props["summary"]["maxLength"] == MAX_SUMMARY_CHARS
    assert props["points"]["items"]["maxLength"] == MAX_POINT_CHARS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_research_output_limits.py -q`
Expected: FAIL — the `MAX_*` constants do not exist.

- [ ] **Step 3: Add the constants and clamping validators**

In `src/app/research/schema.py`, near the existing `CONFIDENCE` / `TIMEFRAMES` constants, add:

```python
# Output limits. These existed only as prose in the prompts ("≤25 words",
# "3-5 bullets") while the tool schemas allowed up to 8 items and unbounded
# strings. Clamped here rather than rejected: dropping a whole plan because
# one bullet ran long would be worse than truncating the bullet.
MAX_SUMMARY_CHARS = 240
MAX_POINT_CHARS = 400
MAX_CASE_ITEMS = 5
```

Add the clamping helpers next to `_wrap_bare_string`:

```python
def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clamp_items(v: object) -> object:
    """Wrap a bare string, cap the list length, and truncate long entries."""
    v = _wrap_bare_string(v)
    if not isinstance(v, list):
        return v
    out = []
    for item in v[:MAX_CASE_ITEMS]:
        out.append(_truncate(item, MAX_POINT_CHARS) if isinstance(item, str) else item)
    return out


def _clamp_summary(v: object) -> object:
    return _truncate(v, MAX_SUMMARY_CHARS) if isinstance(v, str) else v
```

On `EntryExitPlan`, replace the existing `_wrap_prose` validator with:
```python
    _clamp_prose = field_validator(
        "bull_case", "bear_case", "key_risks", mode="before"
    )(_clamp_items)
    _wrap_sources = field_validator("sources_used", mode="before")(_wrap_bare_string)
```
`sources_used` keeps the plain wrapper — it is a provenance list, not prose, and must not be capped at 5 or truncated.

On `LensView`, replace the existing `_wrap_points` validator with:
```python
    _clamp_points = field_validator("points", "revised_points", mode="before")(_clamp_items)
    _wrap_responded = field_validator("responded_to", mode="before")(_wrap_bare_string)
    _clamp_summaries = field_validator("summary", "revised_summary", mode="before")(_clamp_summary)
```
`responded_to` is an enum list of lens names — wrap only, never truncate.

- [ ] **Step 4: Tighten the tool schemas**

In `src/app/research/agents/base.py`, `_lens_tool_input_schema`: import the constants (`from app.research.schema import MAX_CASE_ITEMS, MAX_POINT_CHARS, MAX_SUMMARY_CHARS`), add `"maxLength": MAX_SUMMARY_CHARS` to the `summary` property, and set the `points` property's `items` to `{"type": "string", "maxLength": MAX_POINT_CHARS}` with `"maxItems": MAX_CASE_ITEMS` (leave `minItems: 2`).

In `src/app/research/agents/judge.py`, `judge_tool_input_schema`: change `bull_case` / `bear_case` / `key_risks` from `"maxItems": 8` to `"maxItems": MAX_CASE_ITEMS`, give each one `"items": {"type": "string", "maxLength": MAX_POINT_CHARS}`, and add `"maxLength": MAX_POINT_CHARS` to `entry_rationale`, `primary_exit_rationale`, `runner_exit_rationale`, `invalidation_rationale`.

In `src/app/research/quick.py`'s tool schema: apply the same `maxLength` to the rationale strings and the lens `summary`, and align `bull_case` / `bear_case` / `key_risks` `maxItems` to `MAX_CASE_ITEMS` (currently 4 — raising to 5 is intentional so Quick and Deep agree).

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_research_output_limits.py -q`
Expected: 9 passed.

- [ ] **Step 6: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 568 passed. If an existing test asserted `maxItems == 8` or built a plan with more than 5 case items, update that assertion to the constant and note it in the report.

```bash
git add src/app/research/schema.py src/app/research/agents/base.py \
        src/app/research/agents/judge.py src/app/research/quick.py \
        tests/test_research_output_limits.py
git commit -m "fix(research): enforce output limits in Python, not just in prose

Every length constraint lived in prompt text while the judge tool schema
allowed 8 items and unbounded strings. Constants are now shared, tool
schemas match the prompts, and Pydantic clamps rather than rejects.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: The judge sees valuation

**Files:**
- Modify: `src/app/research/agents/judge.py` (system prompt, user template, `run_judge` signature)
- Modify: `src/app/research/deep.py` (build the digest, pass it through)
- Test: `tests/test_judge_valuation_context.py`

**Interfaces:**
- Consumes: Task 3's `build_panel_digest` / `PanelDigest.valuation_block()`.
- Produces: `run_judge(..., digest: PanelDigest | None = None)`. **Optional with a None default** so every existing caller and test keeps working.

This is the point of the plan. The judge currently receives status, setup, action, style, `last_close`, `atr_14`, 52-week distances, the candidate levels and the lens prose — and no valuation at all, so it cannot check "the price already embeds this story" against a number.

- [ ] **Step 1: Write the failing test**

Create `tests/test_judge_valuation_context.py`:

```python
"""The judge receives valuation context and is told to reconcile it."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.digest import build_panel_digest
from app.analysis.schema import (
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    TickerResearchView,
    ValuationPanel,
)
from app.research.agents import judge as judge_mod

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _view():
    return TickerResearchView(
        ticker="NVDA",
        as_of=AS_OF,
        market=MarketSnapshotPanel(as_of=AS_OF, last_close=100.0),
        valuation=ValuationPanel(forward_pe=41.0, revenue_growth_yoy=0.25, fetched_at=AS_OF),
        indicators=IndicatorPanel(rsi_14=71.0, atr_14=3.5),
        levels=LevelsPanel(),
    )


def test_user_template_has_a_valuation_slot():
    assert "{valuation_block}" in judge_mod.USER_TEMPLATE


def test_system_prompt_instructs_reconciliation():
    prompt = judge_mod.SYSTEM_PROMPT.lower()
    assert "valuation" in prompt


def test_formatted_message_contains_the_valuation_numbers():
    digest = build_panel_digest(_view())
    block = digest.valuation_block()
    assert "forward_pe" in block
    assert "41" in block


def test_missing_digest_renders_an_explicit_placeholder():
    """run_judge must stay callable without a digest — existing callers pass none."""
    block = judge_mod._valuation_block_for(None)
    assert "not available" in block.lower() or "no valuation" in block.lower()


def test_digest_block_is_used_when_present():
    digest = build_panel_digest(_view())
    block = judge_mod._valuation_block_for(digest)
    assert "forward_pe" in block
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_valuation_context.py -q`
Expected: FAIL — no `{valuation_block}` slot and no `_valuation_block_for`.

- [ ] **Step 3: Wire the judge**

In `src/app/research/agents/judge.py`:

Add near the other imports:
```python
from app.analysis.digest import PanelDigest
```

Add a helper next to `_format_candidate_levels`. It must render the **unavailable** valuation fields as well as the present ones: `PanelDigest.valuation_block()` renders only present measures, and the design goal is that absence is visible rather than inferred — otherwise the judge cannot distinguish "P/E is missing" from "P/E is fine".

```python
def _valuation_block_for(digest: PanelDigest | None) -> str:
    """Render the digest's valuation measures, naming what was unavailable.

    A reader that sees only present fields cannot tell a missing multiple
    from a healthy one, so absent fields are listed explicitly.
    """
    if digest is None:
        return "(valuation context not available for this run)"

    block = digest.valuation_block()
    absent = [
        f.split(".", 1)[1] for f in digest.missing if f.startswith("valuation.")
    ]
    if absent:
        block = f"{block}\n(unavailable, do not infer a value: {', '.join(absent)})"
    return block
```

Add a test for this alongside the others in Step 1:
```python
def test_valuation_block_names_unavailable_fields():
    """Absence must be visible to the judge, not inferred from silence."""
    digest = build_panel_digest(_view())
    block = judge_mod._valuation_block_for(digest)
    assert "unavailable" in block.lower()
    assert "trailing_pe" in block  # present in `missing`, absent from measures
```

Add a rule to `SYSTEM_PROMPT`, after the existing confidence rule (keep the numbering contiguous with whatever is already there):
```
N. VALUATION. The user message carries a valuation block with explicit units.
   Where it is present, reconcile it against the technical read: say plainly
   whether the price already embeds the bull case. Cite the specific field
   and number you are relying on (e.g. "forward_pe 41 vs sector norm"). Never
   restate a number without naming its field. If the block says valuation is
   unavailable, say so rather than inferring it.
```

Add the slot to `USER_TEMPLATE`, immediately before the candidate-levels section:
```
Valuation (units stated per line; do not assume a convention):
{valuation_block}
```

In `_format_user_message`, accept `digest: PanelDigest | None = None` and pass `valuation_block=_valuation_block_for(digest)` into the `.format(...)` call.

In `run_judge`, add the keyword-only parameter `digest: PanelDigest | None = None` and thread it into `_format_user_message`.

- [ ] **Step 4: Pass the digest from Deep mode**

In `src/app/research/deep.py`, before the `run_judge` call, build the digest from the packet and pass it:

```python
    from app.analysis.digest import build_panel_digest

    digest = build_panel_digest(
        packet.view,
        fundamentals=packet.fundamentals_extended,
        peers=packet.peer_comparison,
    )

    judge_result = run_judge(
        packet=packet,
        lenses=lenses,
        analyst_results=analyst_results,
        client=client,
        digest=digest,
    )
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_judge_valuation_context.py tests/test_research_deep.py -q`
Expected: all pass. `test_research_deep.py` exercises the orchestration with a mocked client; if it asserts on the exact user-message text, update that assertion and note it in the report.

- [ ] **Step 6: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 573 passed.

```bash
git add src/app/research/agents/judge.py src/app/research/deep.py \
        tests/test_judge_valuation_context.py
git commit -m "feat(research): the judge finally sees valuation

The synthesis step that writes the plan received technicals and lens prose
but no valuation, so it could not check whether the price already embedded
the bull case. It now gets the unit-tagged valuation block and is told to
reconcile it and cite the field it relies on.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Document what shipped

**Files:**
- Modify: `docs/design/thematic-agent.md` (status header + a "what shipped" note)

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: a design doc that states which steps are built and which remain deferred.

- [ ] **Step 1: Update the status block**

At the top of `docs/design/thematic-agent.md`, change the status line to:

```markdown
**Status:** Steps 1-2 implemented 2026-08-05 (see §5.1). Step 3 (thematic lens) deferred — open questions in §7 unanswered.
```

- [ ] **Step 2: Add a shipped note at the end of §5.1**

Append to the §5.1 "v1 scope" section:

```markdown
**Shipped 2026-08-05 (Steps 1-2):** `ttl_cache` replacing the two unbounded
`lru_cache`s (15min metadata / 6h earnings); `ValuationPanel.fetched_at`;
`PanelDigest` with per-field unit tags and an explicit `missing` list;
structural output limits enforced in Pydantic and mirrored into the tool
schemas; and the valuation block passed to the judge with a reconciliation
rule. Step 3 remains unbuilt: no thematic lens, no theme brief, no theme
cache tables, and no valuation-derived price level.
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m pytest -q` — expected 573 passed, unchanged.

```bash
git add docs/design/thematic-agent.md
git commit -m "docs: record Steps 1-2 as shipped in the thematic-agent design

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
