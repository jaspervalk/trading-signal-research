"""Renderers for ScreenResult — table (stdout), JSON, CSV."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Optional

from app.config import REPO_ROOT
from app.screener.schema import ScreenResult, ScreenRow

SCREENS_DIR = REPO_ROOT / "data" / "screens"


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "  -  "
    return f"{x * 100:+5.1f}%"


def _fmt_ratio(x: Optional[float], width: int = 5, places: int = 1) -> str:
    if x is None:
        return "  -  "
    return f"{x:>{width}.{places}f}"


def _fmt_int(x: Optional[int], width: int = 4) -> str:
    if x is None:
        return f"{'-':>{width}}"
    return f"{x:>{width}d}"


def _row_dict(r: ScreenRow) -> dict[str, object]:
    m = r.metrics
    return {
        "ticker": r.ticker,
        "sector": m.sector or "",
        "fwd_pe": m.forward_pe,
        "peg": m.peg_ratio,
        "ev_ebitda": m.ev_to_ebitda,
        "rev_growth": m.revenue_growth_yoy,
        "earn_growth": m.earnings_growth_yoy,
        "gross_margin": m.gross_margin,
        "roe": m.roe,
        "debt_equity": m.debt_to_equity,
        "pct_vs_200d": m.pct_vs_200d,
        "rs_vs_spy_3mo": m.rs_vs_spy_3mo,
        "days_to_earnings": m.days_to_earnings,
        "filters_passed": ",".join(r.filters_passed),
        "creator_mentions": m.creator_mentions,
        "avg_creator_confidence": m.avg_creator_confidence,
        "flags": ",".join(r.flags),
        "data_quality": ",".join(m.data_quality),
        "error": r.error or "",
    }


# ---------------------------------------------------------------------------
# Table


def render_table(
    result: ScreenResult,
    *,
    only_passing: bool = True,
    limit: int | None = None,
) -> str:
    """Pretty-printed monospace table. `only_passing=True` hides rows that
    didn't pass any filter."""
    rows = result.passing if only_passing else result.rows
    if limit is not None:
        rows = rows[:limit]

    out = io.StringIO()
    out.write(
        f"# Screen run at {result.as_of.isoformat()}\n"
        f"#   universe={result.universe_size}  completed={result.n_completed}  "
        f"errors={result.n_errors}  passing={len(result.passing)}\n"
    )
    if result.config.sector:
        out.write(f"#   sector filter: {result.config.sector}\n")
    if result.config.enabled_filters:
        out.write(f"#   filters: {','.join(result.config.enabled_filters)}\n")
    out.write("\n")

    header = (
        f"{'TICKER':<6} {'SECTOR':<22} {'FWD_PE':>7} {'PEG':>5} {'REV%':>6} "
        f"{'GM%':>6} {'ROE%':>6} {'D/E':>5} {'200d%':>7} {'RS3m':>6} "
        f"{'EARN_D':>6} {'MENTNS':>6} {'PASSED':<20} FLAGS"
    )
    out.write(header + "\n")
    out.write("-" * len(header) + "\n")
    for r in rows:
        m = r.metrics
        line = (
            f"{r.ticker:<6} {(m.sector or '')[:22]:<22} "
            f"{_fmt_ratio(m.forward_pe, 7, 1)} {_fmt_ratio(m.peg_ratio, 5, 2)} "
            f"{_fmt_pct(m.revenue_growth_yoy):>6} "
            f"{_fmt_pct(m.gross_margin):>6} {_fmt_pct(m.roe):>6} "
            f"{_fmt_ratio(m.debt_to_equity, 5, 2)} "
            f"{_fmt_pct(m.pct_vs_200d):>7} {_fmt_pct(m.rs_vs_spy_3mo):>6} "
            f"{_fmt_int(m.days_to_earnings, 6)} "
            f"{_fmt_int(m.creator_mentions, 6)} "
            f"{','.join(r.filters_passed):<20} {','.join(r.flags)}"
        )
        out.write(line + "\n")

    if not rows:
        out.write("(no rows)\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# JSON / CSV


def to_json(result: ScreenResult) -> str:
    """Full ScreenResult as a JSON string. Pretty-printed."""
    payload = json.loads(result.model_dump_json())
    return json.dumps(payload, indent=2, default=str)


def to_csv(result: ScreenResult, *, only_passing: bool = True) -> str:
    """CSV string. Header matches the table columns + a few extras."""
    rows = result.passing if only_passing else result.rows
    if not rows:
        return ""
    buf = io.StringIO()
    fieldnames = list(_row_dict(rows[0]).keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(_row_dict(r))
    return buf.getvalue()


def save_json(
    result: ScreenResult,
    *,
    base_dir: Path = SCREENS_DIR,
    when: datetime | None = None,
) -> Path:
    """Persist the full result to `data/screens/YYYY-MM-DD.json`."""
    base_dir.mkdir(parents=True, exist_ok=True)
    day = (when or datetime.now(tz=UTC)).date()
    path = base_dir / f"{day.isoformat()}.json"
    path.write_text(to_json(result))
    return path


__all__ = [
    "SCREENS_DIR",
    "render_table",
    "save_json",
    "to_csv",
    "to_json",
]
