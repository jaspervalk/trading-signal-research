from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Watchlist
from apps.api.app.deps import db_session
from apps.api.app.schemas import WatchlistIn, WatchlistOut

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(
    entity_type: str | None = Query(None),
    session: Session = Depends(db_session),
) -> list[Watchlist]:
    q = select(Watchlist).order_by(Watchlist.pinned_at.desc())
    if entity_type:
        q = q.where(Watchlist.entity_type == entity_type)
    return list(session.execute(q).scalars().all())


@router.post("", response_model=WatchlistOut, status_code=201)
def pin(payload: WatchlistIn, session: Session = Depends(db_session)) -> Watchlist:
    w = Watchlist(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        note=payload.note,
    )
    session.add(w)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "already on watchlist")
    session.refresh(w)
    return w


@router.delete("/{watchlist_id}", status_code=204)
def unpin(watchlist_id: int, session: Session = Depends(db_session)) -> None:
    w = session.get(Watchlist, watchlist_id)
    if w is None:
        raise HTTPException(404, "not found")
    session.delete(w)
    session.commit()
