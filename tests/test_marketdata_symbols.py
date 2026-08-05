"""One canonical spelling per ticker, across every loader and the price cache.

The bug this pins: `configs/universe.csv` stored `BRK.B` while
`configs/screen_universe.csv` stored `BRK-B`, and yfinance answers only to the
dash form. A call extracted as `BRK.B` therefore passed universe validation,
fetched zero bars, and produced no outcome — silently, and indistinguishably
from a call that simply never activated.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.market import yfinance_client
from app.marketdata.symbols import canonical_symbol, display_symbol, symbol_variants
from app.normalize.tickers import load_universe
from app.screener.universe import load_universe as load_screen_universe


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BRK.B", "BRK-B"),
        ("BRK-B", "BRK-B"),
        ("brk.b", "BRK-B"),
        ("  aapl  ", "AAPL"),
        ("BF.B", "BF-B"),
        ("AAPL", "AAPL"),
    ],
)
def test_canonical_symbol_collapses_share_class_spellings(raw, expected):
    assert canonical_symbol(raw) == expected


def test_display_symbol_is_the_human_spelling():
    assert display_symbol("BRK-B") == "BRK.B"
    assert display_symbol("AAPL") == "AAPL"


def test_symbol_variants_covers_both_spellings():
    assert symbol_variants("BRK.B") == {"BRK-B", "BRK.B"}
    assert symbol_variants("AAPL") == {"AAPL"}


def test_canonical_is_idempotent():
    """Round-tripping must not drift — this runs on stored tickers."""
    for raw in ("BRK.B", "BRK-B", "AAPL", "bf.b"):
        once = canonical_symbol(raw)
        assert canonical_symbol(once) == once


# --------------------------------------------------------------------------- #
# The loaders


def _write_universe(path: Path, ticker: str) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "sector"])
        w.writeheader()
        w.writerow({"ticker": ticker, "name": "Berkshire Hathaway", "sector": "Financials"})


@pytest.mark.parametrize("stored", ["BRK.B", "BRK-B"])
@pytest.mark.parametrize("queried", ["BRK.B", "BRK-B", "brk.b"])
def test_extractor_universe_membership_survives_either_spelling(tmp_path, stored, queried):
    """Whichever way the CSV spells it, either query form must be a member."""
    csv_path = tmp_path / "universe.csv"
    _write_universe(csv_path, stored)

    universe = load_universe(csv_path)

    assert universe.has(queried), f"{queried!r} not found when CSV stored {stored!r}"


@pytest.mark.parametrize("stored", ["BRK.B", "BRK-B"])
def test_extractor_universe_keys_are_canonical(tmp_path, stored):
    """Keys must match what the market layer will request, not what the CSV said."""
    csv_path = tmp_path / "universe.csv"
    _write_universe(csv_path, stored)

    assert load_universe(csv_path).tickers == {"BRK-B"}


@pytest.mark.parametrize("stored", ["BRK.B", "BRK-B"])
def test_screen_universe_normalises_to_the_fetchable_form(tmp_path, stored):
    path = tmp_path / "screen_universe.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "sector", "source"])
        w.writeheader()
        w.writerow(
            {"ticker": stored, "name": "Berkshire Hathaway", "sector": "Financials", "source": "seed"}
        )

    assert [e.ticker for e in load_screen_universe(path)] == ["BRK-B"]


def test_the_two_shipped_universes_agree_after_normalisation():
    """The regression itself: the repo's own two CSVs disagreed on BRK.

    Both files legitimately hold different *sets* of tickers, so this asserts
    only that any symbol appearing in both resolves to one identity.
    """
    extractor = load_universe().tickers
    screen = {e.ticker for e in load_screen_universe()}

    # Every symbol is already canonical, so a dot form anywhere is a regression.
    assert not {t for t in extractor if "." in t}
    assert not {t for t in screen if "." in t}

    overlap = extractor & screen
    assert "BRK-B" in overlap, "BRK should now resolve identically in both universes"


# --------------------------------------------------------------------------- #
# The price cache


def test_price_cache_is_one_file_per_company(tmp_path, monkeypatch):
    """`BRK.B` and `BRK-B` must not create two half-populated parquet files."""
    monkeypatch.setattr(yfinance_client, "_CACHE_DIR", tmp_path)

    assert yfinance_client._cache_path("BRK.B") == yfinance_client._cache_path("BRK-B")
    assert yfinance_client._cache_path("BRK.B").name == "BRK-B.parquet"


def test_price_cache_path_stays_filesystem_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(yfinance_client, "_CACHE_DIR", tmp_path)
    assert "/" not in yfinance_client._cache_path("A/B").name
