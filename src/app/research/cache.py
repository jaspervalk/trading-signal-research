"""DB-backed cache for `EntryExitPlan` results.

Keyed by `(ticker, mode, day_key)` where `day_key` is the UTC `YYYY-MM-DD`.
Same-day re-clicks return the cached row; new days trigger a fresh run.

The plan JSON is stored as Text on the `research_plans` table; we never
query inside the JSON. Headline fields (confidence / timeframe / cost) are
denormalised for cheap listing without parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchPlan
from app.research.schema import EntryExitPlan


def _day_key(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).date().isoformat()


def get_cached(
    *,
    session: Session,
    ticker: str,
    mode: str,
    when: datetime | None = None,
) -> EntryExitPlan | None:
    """Return the cached plan for `(ticker, mode, day_of_when)` or None."""
    when = when or datetime.now(tz=UTC)
    key = _day_key(when)
    row = session.execute(
        select(ResearchPlan)
        .where(ResearchPlan.ticker == ticker.upper())
        .where(ResearchPlan.mode == mode)
        .where(ResearchPlan.day_key == key)
        .order_by(ResearchPlan.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return EntryExitPlan.model_validate_json(row.plan_json)


def store(*, session: Session, plan: EntryExitPlan) -> ResearchPlan:
    """Persist a plan. Same-key insert overwrites the previous row's
    headline columns + plan_json, but we keep history by appending — the
    `get_cached` query takes the most recent id.
    """
    row = ResearchPlan(
        ticker=plan.ticker.upper(),
        mode=plan.mode,
        day_key=_day_key(plan.as_of),
        confidence=plan.confidence,
        timeframe=plan.timeframe,
        cost_usd=plan.cost_usd,
        duration_ms=plan.duration_ms,
        plan_json=plan.model_dump_json(),
    )
    session.add(row)
    session.flush()
    return row


__all__ = ["get_cached", "store"]
