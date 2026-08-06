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


def test_sort_key_ranks_higher_torque_first():
    from app.supply.metrics import SupplyMetrics
    from app.supply.screen import ScreenResult

    low = ScreenResult(
        ticker="LOW",
        cik=1,
        metrics=SupplyMetrics(
            quarters_of_history=24,
            sufficient_history=True,
            earnings_torque=0.1,
            capital_intensity=0.6,
        ),
        passed=False,
    )
    high = ScreenResult(
        ticker="HIGH",
        cik=2,
        metrics=SupplyMetrics(
            quarters_of_history=24,
            sufficient_history=True,
            earnings_torque=0.9,
            capital_intensity=0.6,
        ),
        passed=True,
    )
    unscored = ScreenResult(ticker="ZZZ", reasons=["no CIK found for ticker 'ZZZ'"])

    ordered = sorted([low, unscored, high], key=_supply_screen_sort_key)
    assert [r.ticker for r in ordered] == ["HIGH", "LOW", "ZZZ"]
