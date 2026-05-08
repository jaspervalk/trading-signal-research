"""Persist `LensView` reads to `LensSnapshot` rows for later accuracy scoring.

Idempotent on (ticker, as_of, mode, lens_name). Safe to call from cached
Deep runs (cache hit re-emits the same lenses; recording skips duplicates).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensSnapshot
from app.research.schema import LensView


def record_lens_snapshots(
    session: Session,
    *,
    plan_id: int | None,
    ticker: str,
    as_of: datetime,
    mode: str,  # "quick" | "deep"
    lenses: Sequence[LensView],
    sources_used: Sequence[str],
    durations_ms: Sequence[int] = (),
    costs_usd: Sequence[float] = (),
) -> list[int]:
    """Write one `LensSnapshot` per lens. Returns the row ids (existing or new).

    Idempotent: a duplicate `(ticker, as_of, mode, lens_name)` is detected
    and the existing row's id is returned without rewriting. This lets the
    cache layer re-record on cache-hit without polluting the table.
    """
    out: list[int] = []
    sources_json = json.dumps(list(sources_used))
    for i, lens in enumerate(lenses):
        existing = session.execute(
            select(LensSnapshot).where(
                LensSnapshot.ticker == ticker,
                LensSnapshot.as_of == as_of,
                LensSnapshot.mode == mode,
                LensSnapshot.lens_name == lens.name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing.id)
            continue

        row = LensSnapshot(
            plan_id=plan_id,
            ticker=ticker,
            as_of=as_of,
            mode=mode,
            lens_name=lens.name,
            direction=lens.direction,
            conviction=lens.conviction,
            summary=lens.summary,
            points_json=json.dumps(list(lens.points)),
            sources_used=sources_json,
            cost_usd=costs_usd[i] if i < len(costs_usd) else 0.0,
            duration_ms=durations_ms[i] if i < len(durations_ms) else 0,
        )
        session.add(row)
        session.flush()  # populate row.id without committing
        out.append(row.id)
    return out


__all__ = ["record_lens_snapshots"]
