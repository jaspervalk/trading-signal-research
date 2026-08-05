"""tsr pf commands wire the Typer sub-app to the portfolio service."""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli import app, pf_cell

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


# `pf list` runs against the real DB via `app.db.session_scope()`, which is
# not the `session` fixture's in-memory engine and which this suite must not
# write to (see CLAUDE.md / the fix brief's safety rule). So instead of
# invoking `pf list` end-to-end, this exercises the row/total formatting
# helper directly — it is what actually renders "nan" vs "—", and it is a
# pure function with no DB dependency at all.
def test_pf_cell_renders_none_as_em_dash():
    assert pf_cell(None, 10) == f"{'—':>10}"


def test_pf_cell_renders_nan_as_em_dash():
    assert pf_cell(float("nan"), 10) == f"{'—':>10}"


def test_pf_cell_renders_number_right_aligned():
    assert pf_cell(12.5, 10) == f"{12.5:>10.2f}"
