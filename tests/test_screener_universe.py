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


def test_load_universe_reads_end_market_column(tmp_path: Path):
    csv = tmp_path / "u.csv"
    csv.write_text(
        "ticker,name,sector,source,end_market\n"
        "TROX,Tronox,specialty_chemicals,seed,construction\n"
        "APD,Air Products,industrial_gas,seed,\n"
    )
    entries = load_universe(csv)
    assert entries[0].end_market == "construction"
    assert entries[1].end_market == ""  # blank stays blank, not a phantom group


def test_load_universe_defaults_end_market_when_column_absent(tmp_path: Path):
    """Older screen_universe.csv-style files with no end_market column at
    all must still load cleanly."""
    csv = tmp_path / "u.csv"
    csv.write_text("ticker,name,sector,source\nAAPL,Apple,Technology,sp500\n")
    entries = load_universe(csv)
    assert entries[0].end_market == ""


def test_write_universe_roundtrip_preserves_end_market(tmp_path: Path):
    entries = [UniverseEntry("TROX", "Tronox", "specialty_chemicals", "seed", "construction")]
    csv = tmp_path / "u.csv"
    write_universe(entries, csv)
    reloaded = load_universe(csv)
    assert reloaded == entries
    assert reloaded[0].end_market == "construction"
