"""`tsr supply-screen` CLI: help text only — no network, no DB.

Real end-to-end runs (EDGAR + yfinance + SEC throttle) are exercised
manually per the task brief, not in the suite; `app.supply.screen` already
has its own network-free unit coverage in `tests/test_supply_screen.py`.
"""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli import app, _supply_screen_sort_key

runner = CliRunner()


def test_supply_screen_help_lists_options():
    result = runner.invoke(app, ["supply-screen", "--help"])
    assert result.exit_code == 0
    for option in ("--limit", "--universe", "--json"):
        assert option in result.stdout


def test_supply_screen_registered_on_main_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "supply-screen" in result.stdout


def _metrics(**overrides):
    from app.supply.metrics import SupplyMetrics

    base = dict(
        quarters_of_history=24,
        sufficient_history=True,
        capital_intensity=0.6,
    )
    base.update(overrides)
    return SupplyMetrics(**base)


def test_sort_key_ranks_a_passing_row_above_a_higher_torque_failing_row():
    """Change 1: the gate must be respected. A near-miss with a higher
    torque must NOT outrank a row that actually passed everything."""
    from app.supply.screen import ScreenResult

    passing_low_torque = ScreenResult(
        ticker="WLK",
        cik=1,
        metrics=_metrics(earnings_torque=0.27, survivability_quarters=25.0),
        passed=True,
        reasons=[],
    )
    failing_high_torque = ScreenResult(
        ticker="KRO",
        cik=2,
        metrics=_metrics(earnings_torque=0.9, survivability_quarters=0.6),
        passed=False,
        reasons=["survivability_quarters=0.6 < 8"],
    )

    ordered = sorted([failing_high_torque, passing_low_torque], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["WLK", "KRO"]


def test_sort_key_sinks_a_survivability_only_failure_below_an_equally_failing_row():
    """Within near-misses, a row failing ONLY on survivability must sink
    below an equally-failing row that fails on something else -- a company
    that can't outlast the cycle is disqualified in kind, not degree."""
    from app.supply.screen import ScreenResult

    survivability_only = ScreenResult(
        ticker="KRO",
        cik=1,
        metrics=_metrics(earnings_torque=0.9, survivability_quarters=0.6),
        passed=False,
        reasons=["survivability_quarters=0.6 < 8"],
    )
    fails_other_criterion = ScreenResult(
        ticker="OTH",
        cik=2,
        metrics=_metrics(earnings_torque=0.9, survivability_quarters=25.0),
        passed=False,
        reasons=["gm_volatility_pp=2.0 < 5.0"],
    )

    ordered = sorted([survivability_only, fails_other_criterion], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["OTH", "KRO"]


def test_sort_key_survivability_only_failure_sinks_even_below_lower_torque_other_failure():
    """The tier boundary dominates torque within near-misses: a
    survivability-only failure sinks below an "other" failure even when its
    own torque is higher."""
    from app.supply.screen import ScreenResult

    survivability_only_high_torque = ScreenResult(
        ticker="KRO",
        cik=1,
        metrics=_metrics(earnings_torque=5.0, survivability_quarters=0.6),
        passed=False,
        reasons=["survivability_quarters=0.6 < 8"],
    )
    other_failure_low_torque = ScreenResult(
        ticker="OTH",
        cik=2,
        metrics=_metrics(earnings_torque=0.3, survivability_quarters=25.0),
        passed=False,
        reasons=["capital_intensity=0.1 < 0.5"],
    )

    ordered = sorted(
        [survivability_only_high_torque, other_failure_low_torque],
        key=_supply_screen_sort_key,
    )
    assert [r.ticker for r in ordered] == ["OTH", "KRO"]


def test_sort_key_unscored_rows_sink_below_every_scored_row():
    from app.supply.screen import ScreenResult

    passing = ScreenResult(
        ticker="WLK", cik=1, metrics=_metrics(earnings_torque=0.1), passed=True, reasons=[]
    )
    failing = ScreenResult(
        ticker="OTH",
        cik=2,
        metrics=_metrics(earnings_torque=0.0),
        passed=False,
        reasons=["capital_intensity=0.1 < 0.5"],
    )
    unscored = ScreenResult(ticker="ZZZ", reasons=["no CIK found for ticker 'ZZZ'"])

    ordered = sorted([unscored, failing, passing], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["WLK", "OTH", "ZZZ"]


def test_sort_key_within_a_tier_ranks_higher_torque_first():
    from app.supply.screen import ScreenResult

    low = ScreenResult(
        ticker="LOW", cik=1, metrics=_metrics(earnings_torque=0.1), passed=True, reasons=[]
    )
    high = ScreenResult(
        ticker="HIGH", cik=2, metrics=_metrics(earnings_torque=0.9), passed=True, reasons=[]
    )
    ordered = sorted([low, high], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["HIGH", "LOW"]


def test_sort_key_is_deterministic_via_ticker_tie_break():
    from app.supply.screen import ScreenResult

    a = ScreenResult(
        ticker="AAA", cik=1, metrics=_metrics(earnings_torque=0.5), passed=True, reasons=[]
    )
    b = ScreenResult(
        ticker="BBB", cik=2, metrics=_metrics(earnings_torque=0.5), passed=True, reasons=[]
    )
    ordered1 = sorted([b, a], key=_supply_screen_sort_key)
    ordered2 = sorted([a, b], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered1] == ["AAA", "BBB"]
    assert [r.ticker for r in ordered1] == [r.ticker for r in ordered2]


def test_sort_key_insufficient_history_near_miss_is_not_treated_as_survivability_only():
    """An insufficient-history row's single reason string doesn't start
    with `survivability_quarters=`, so it belongs in the "other" near-miss
    tier, not the survivability-only tier."""
    from app.supply.screen import ScreenResult

    insufficient_history = ScreenResult(
        ticker="THIN",
        cik=1,
        metrics=_metrics(sufficient_history=False, quarters_of_history=10),
        passed=False,
        reasons=["insufficient_history: 10 quarters (need >= 24)"],
    )
    survivability_only = ScreenResult(
        ticker="KRO",
        cik=2,
        metrics=_metrics(earnings_torque=0.9, survivability_quarters=0.6),
        passed=False,
        reasons=["survivability_quarters=0.6 < 8"],
    )
    ordered = sorted([survivability_only, insufficient_history], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["THIN", "KRO"]
