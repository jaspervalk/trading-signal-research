"""Universe: load + dedup + sector filter + write round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.screener.universe import (
    UniverseEntry,
    load_universe,
    write_universe,
)


def test_load_universe_reads_csv(tmp_path: Path):
    csv = tmp_path / "u.csv"
    csv.write_text(
        "ticker,name,sector,source\n"
        "AAPL,Apple,Technology,sp500\n"
        "TSLA,Tesla,Consumer Discretionary,sp500\n"
    )
    entries = load_universe(csv)
    assert [e.ticker for e in entries] == ["AAPL", "TSLA"]
    assert entries[0].sector == "Technology"


def test_load_universe_filters_by_sector(tmp_path: Path):
    csv = tmp_path / "u.csv"
    csv.write_text(
        "ticker,name,sector,source\n"
        "AAPL,Apple,Technology,sp500\n"
        "JPM,JPMorgan,Financials,sp500\n"
        "NVDA,NVIDIA,Technology,both\n"
    )
    techs = load_universe(csv, sector="Technology")
    assert {e.ticker for e in techs} == {"AAPL", "NVDA"}


def test_sector_filter_is_case_insensitive(tmp_path: Path):
    csv = tmp_path / "u.csv"
    csv.write_text(
        "ticker,name,sector,source\n"
        "AAPL,Apple,Technology,sp500\n"
    )
    out = load_universe(csv, sector="technology")
    assert len(out) == 1


def test_load_universe_skips_empty_ticker_rows(tmp_path: Path):
    csv = tmp_path / "u.csv"
    csv.write_text(
        "ticker,name,sector,source\n"
        ",,,sp500\n"
        "AAPL,Apple,Technology,sp500\n"
    )
    out = load_universe(csv)
    assert [e.ticker for e in out] == ["AAPL"]


def test_load_universe_raises_when_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_universe(tmp_path / "missing.csv")


def test_write_universe_roundtrip(tmp_path: Path):
    entries = [
        UniverseEntry("AAPL", "Apple", "Technology", "sp500"),
        UniverseEntry("NVDA", "NVIDIA", "Technology", "both"),
    ]
    csv = tmp_path / "u.csv"
    write_universe(entries, csv)
    reloaded = load_universe(csv)
    assert reloaded == entries
