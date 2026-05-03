from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creator, CreatorScorecard
from apps.api.app.deps import db_session
from apps.api.app.schemas import LeaderboardRow, ScorecardOut

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", response_model=list[LeaderboardRow])
def get_leaderboard(
    horizon: str = Query("5d"),
    window_label: str = Query("all"),
    session: Session = Depends(db_session),
) -> list[LeaderboardRow]:
    """One row per creator at the requested horizon + window. Sorted by mean_excess_return desc."""
    rows = session.execute(
        select(Creator.id, Creator.display_name, CreatorScorecard)
        .join(CreatorScorecard, CreatorScorecard.creator_id == Creator.id)
        .where(CreatorScorecard.horizon == horizon)
        .where(CreatorScorecard.window_label == window_label)
    ).all()

    out = []
    for creator_id, name, sc in rows:
        out.append(
            LeaderboardRow(
                creator_id=creator_id,
                creator_name=name,
                n_calls=sc.n_calls,
                n_activated=sc.n_activated,
                n_unique_tickers=sc.n_unique_tickers,
                hit_rate=sc.hit_rate,
                hit_rate_lower_ci=sc.hit_rate_lower_ci,
                hit_rate_upper_ci=sc.hit_rate_upper_ci,
                mean_return=sc.mean_return,
                mean_excess_return=sc.mean_excess_return,
                expectancy_unconditional=sc.expectancy_unconditional,
                sharpe_like=sc.sharpe_like,
            )
        )
    out.sort(
        key=lambda r: (r.mean_excess_return if r.mean_excess_return is not None else -1e9),
        reverse=True,
    )
    return out


@router.get("/scorecards/{creator_id}", response_model=list[ScorecardOut])
def scorecards_for_creator(
    creator_id: int, session: Session = Depends(db_session)
) -> list[CreatorScorecard]:
    return list(
        session.execute(
            select(CreatorScorecard)
            .where(CreatorScorecard.creator_id == creator_id)
            .order_by(CreatorScorecard.window_label, CreatorScorecard.horizon)
        ).scalars().all()
    )
