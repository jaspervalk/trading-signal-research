from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creator
from apps.api.app.deps import db_session
from apps.api.app.schemas import CreatorOut

router = APIRouter(prefix="/creators", tags=["creators"])


@router.get("", response_model=list[CreatorOut])
def list_creators(
    active_only: bool = False,
    session: Session = Depends(db_session),
) -> list[Creator]:
    q = select(Creator).order_by(Creator.display_name)
    if active_only:
        q = q.where(Creator.active.is_(True))
    return list(session.execute(q).scalars().all())


@router.get("/{creator_id}", response_model=CreatorOut)
def get_creator(creator_id: int, session: Session = Depends(db_session)) -> Creator:
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise HTTPException(404, detail="creator not found")
    return creator
