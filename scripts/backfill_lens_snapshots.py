"""One-shot backfill: replay cached ResearchPlan.lenses → LensSnapshot rows.

Idempotent (record_lens_snapshots dedupes on the run-key). Run once after
deploying Phase 1; subsequent Deep/Quick runs record live.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db import session_scope
from app.models import ResearchPlan
from app.research.lens_recording import record_lens_snapshots
from app.research.schema import LensView


def _resolve_as_of(plan: ResearchPlan, payload: dict) -> datetime:
    """ResearchPlan has no `as_of` column; derive it from JSON or computed_at."""
    raw = payload.get("as_of")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    computed = getattr(plan, "computed_at", None)
    if isinstance(computed, datetime):
        return computed if computed.tzinfo else computed.replace(tzinfo=timezone.utc)
    # Last-resort: midnight UTC for the plan's day_key.
    return datetime.fromisoformat(f"{plan.day_key}T00:00:00+00:00")


def main():
    with session_scope() as session:
        plans = session.query(ResearchPlan).all()
        n_recorded = 0
        n_seen = 0
        for plan in plans:
            if not plan.plan_json:
                continue
            try:
                payload = json.loads(plan.plan_json)
            except Exception:
                continue
            lenses_raw = payload.get("lenses") or []
            if not lenses_raw:
                continue
            n_seen += 1
            lenses = [
                LensView(
                    name=l["name"],
                    direction=l.get("direction", "neutral"),
                    conviction=l.get("conviction", "low"),
                    summary=l.get("summary", ""),
                    points=l.get("points", []),
                )
                for l in lenses_raw
            ]
            as_of = _resolve_as_of(plan, payload)
            ids = record_lens_snapshots(
                session,
                plan_id=plan.id,
                ticker=plan.ticker,
                as_of=as_of,
                mode=plan.mode or "quick",
                lenses=lenses,
                sources_used=payload.get("sources_used", []),
                durations_ms=[plan.duration_ms or 0] * len(lenses),
                costs_usd=[round((plan.cost_usd or 0) / max(len(lenses), 1), 6)] * len(lenses),
            )
            n_recorded += len(ids)
        print(
            f"Backfilled {n_recorded} lens snapshots from {n_seen} plans "
            f"with lens data (total cached plans: {len(plans)})."
        )


if __name__ == "__main__":
    main()
