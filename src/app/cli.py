"""Operator CLI. Run via `tsr <command>` after `pip install -e .`."""

from __future__ import annotations

import typer

from app.db import create_all
from app.ingest.run import ingest_all
from app.logging import configure_logging, get_logger

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = get_logger(__name__)


@app.callback()
def main() -> None:
    """trading-signal-research CLI."""
    configure_logging()


@app.command()
def initdb() -> None:
    """Create all tables in the configured database (dev only — prod uses Alembic)."""
    create_all()
    log.info("cli.initdb.done")


@app.command()
def ingest(
    limit: int = typer.Option(25, help="Max recent videos per creator to consider."),
) -> None:
    """Pull recent videos + transcripts for all active creators."""
    results = ingest_all(limit=limit)
    log.info("cli.ingest.done", results=results)


# Placeholders for future phases. Each prints a one-liner so the CLI shape
# is real from the start, even before the implementations land.

@app.command()
def extract(
    limit: int = typer.Option(0, help="Limit number of Documents processed (0 = no limit)."),
    min_segments: int = typer.Option(
        50,
        help=(
            "Skip documents with fewer than this many transcript segments. "
            "Defaults to 50 to skip empty/very-short videos that produce no useful candidates."
        ),
    ),
    one_pass: bool = typer.Option(
        False, "--one-pass", help="Skip the second validation pass. Faster, less safe."
    ),
) -> None:
    """Run call extractor over new documents. Requires ANTHROPIC_API_KEY."""
    from app.extract.run import run_extraction

    summary = run_extraction(
        limit=limit if limit > 0 else None,
        min_segments=min_segments,
        use_two_pass=not one_pass,
    )
    log.info("cli.extract.done", **summary)


@app.command()
def backtest(
    limit: int = typer.Option(0, help="Limit number of calls processed (0 = no limit)."),
) -> None:
    """Compute OutcomeWindows for accepted calls that haven't been evaluated yet."""
    from app.backtest.run import run_backtest

    summary = run_backtest(limit=limit if limit > 0 else None)
    log.info("cli.backtest.done", **summary)


@app.command()
def score() -> None:
    """Recompute creator scorecards from OutcomeWindow data."""
    from app.scoring.creator import run_scoring

    summary = run_scoring()
    log.info("cli.score.done", **summary)


@app.command()
def rank() -> None:
    """Rank new calls with the trained model. (Phase 5)"""
    typer.echo("rank: not implemented yet (Phase 5)")


if __name__ == "__main__":
    app()
