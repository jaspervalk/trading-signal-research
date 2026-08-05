"""tsr pf commands wire the Typer sub-app to the portfolio service."""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_pf_help_lists_commands():
    result = runner.invoke(app, ["pf", "--help"])
    assert result.exit_code == 0
    for command in ("add", "list", "trades", "rm"):
        assert command in result.stdout


def test_pf_add_help_documents_required_options():
    result = runner.invoke(app, ["pf", "add", "--help"])
    assert result.exit_code == 0
    for option in ("--side", "--qty", "--price", "--date"):
        assert option in result.stdout


def test_pf_add_rejects_malformed_date():
    result = runner.invoke(
        app,
        ["pf", "add", "NVDA", "--side", "buy", "--qty", "1",
         "--price", "100", "--date", "15-07-2026"],
    )
    assert result.exit_code == 1
    assert "YYYY-MM-DD" in result.stdout
    assert "Traceback" not in result.stdout
