from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tag
from apps.api.app.deps import db_session
from apps.api.app.schemas import TagIn, TagOut

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags_for_entity(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    session: Session = Depends(db_session),
) -> list[Tag]:
    return list(
        session.execute(
            select(Tag)
            .where(Tag.entity_type == entity_type)
            .where(Tag.entity_id == entity_id)
            .order_by(Tag.created_at.desc())
        ).scalars().all()
    )


@router.get("/labels", response_model=list[str])
def list_distinct_labels(session: Session = Depends(db_session)) -> list[str]:
    """All distinct tag labels — useful for autocomplete in the UI."""
    rows = session.execute(select(Tag.label).distinct().order_by(Tag.label)).all()
    return [r[0] for r in rows]


@router.post("", response_model=TagOut, status_code=201)
def create_tag(payload: TagIn, session: Session = Depends(db_session)) -> Tag:
    t = Tag(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        label=payload.label,
    )
    session.add(t)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "tag already exists for this entity")
    session.refresh(t)
    return t


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: int, session: Session = Depends(db_session)) -> None:
    t = session.get(Tag, tag_id)
    if t is None:
        raise HTTPException(404, "tag not found")
    session.delete(t)
    session.commit()
