from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import REPO_ROOT
from app.models import GoldLabel
from apps.api.app.deps import db_session
from apps.api.app.schemas import GoldLabelIn, GoldLabelOut

router = APIRouter(prefix="/gold", tags=["gold"])


@router.get("", response_model=list[GoldLabelOut])
def list_gold(session: Session = Depends(db_session)) -> list[GoldLabel]:
    return list(
        session.execute(
            select(GoldLabel).order_by(GoldLabel.created_at.desc())
        ).scalars().all()
    )


@router.get("/{gold_id}", response_model=GoldLabelOut)
def get_gold(gold_id: int, session: Session = Depends(db_session)) -> GoldLabel:
    g = session.get(GoldLabel, gold_id)
    if g is None:
        raise HTTPException(404, "gold label not found")
    return g


@router.post("", response_model=GoldLabelOut, status_code=201)
def create_gold(payload: GoldLabelIn, session: Session = Depends(db_session)) -> GoldLabel:
    g = GoldLabel(**payload.model_dump())
    session.add(g)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "source_key already exists")
    session.refresh(g)
    return g


@router.patch("/{gold_id}", response_model=GoldLabelOut)
def update_gold(
    gold_id: int,
    payload: GoldLabelIn,
    session: Session = Depends(db_session),
) -> GoldLabel:
    g = session.get(GoldLabel, gold_id)
    if g is None:
        raise HTTPException(404, "gold label not found")
    for k, v in payload.model_dump().items():
        setattr(g, k, v)
    g.updated_at = datetime.now(tz=UTC)
    session.commit()
    session.refresh(g)
    return g


@router.delete("/{gold_id}", status_code=204)
def delete_gold(gold_id: int, session: Session = Depends(db_session)) -> None:
    g = session.get(GoldLabel, gold_id)
    if g is None:
        raise HTTPException(404, "gold label not found")
    session.delete(g)
    session.commit()


@router.post("/import", status_code=201)
def import_gold_jsonl(session: Session = Depends(db_session)) -> dict:
    """Bulk-import (or refresh) entries from data/gold/extraction_gold.jsonl."""
    import json

    path = REPO_ROOT / "data" / "gold" / "extraction_gold.jsonl"
    if not path.exists():
        raise HTTPException(404, f"jsonl not found at {path}")

    inserted = 0
    updated = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            row = json.loads(line)
            source_key = row["id"]
            expected = row.get("expected") or {}
            existing = session.scalar(
                select(GoldLabel).where(GoldLabel.source_key == source_key)
            )
            target = existing or GoldLabel(source_key=source_key)
            target.source_text = row["source_text"]
            target.expected_is_call = expected is not None and bool(expected)
            target.expected_ticker = (expected or {}).get("ticker")
            target.expected_direction = (expected or {}).get("direction")
            target.expected_entry_type = (expected or {}).get("entry_type")
            target.expected_entry_price = (expected or {}).get("entry_price")
            target.expected_target_price = (expected or {}).get("target_price")
            target.expected_stop_price = (expected or {}).get("stop_price")
            target.expected_timeframe = (expected or {}).get("timeframe")
            target.notes = row.get("notes", "")
            target.updated_at = datetime.now(tz=UTC)
            if existing is None:
                session.add(target)
                inserted += 1
            else:
                updated += 1
    session.commit()
    return {"inserted": inserted, "updated": updated}
