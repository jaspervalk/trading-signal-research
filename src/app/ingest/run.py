"""Ingestion orchestrator.

Reads creators.yaml, ensures Creator + SourceChannel rows exist, lists recent
documents per channel, and persists Documents + TranscriptSegments idempotently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from app.config import REPO_ROOT
from app.db import session_scope
from app.logging import get_logger
from app.models import Creator, Document, SourceChannel, TranscriptSegment
from app.sources.base import RawDocument, SourceAdapter
from app.sources.youtube import YouTubeAdapter, YouTubeIpBlocked

log = get_logger(__name__)


@dataclass
class CreatorConfig:
    display_name: str
    source_type: str
    external_id: str
    handle: str | None
    url: str | None
    active: bool = True
    notes: str | None = None


def load_creator_configs(path: Path | None = None) -> list[CreatorConfig]:
    cfg_path = path or (REPO_ROOT / "configs" / "creators.yaml")
    if not cfg_path.exists():
        log.warning("ingest.creators_yaml.missing", path=str(cfg_path))
        return []

    with cfg_path.open() as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("creators", [])
    out: list[CreatorConfig] = []
    for c in raw:
        out.append(
            CreatorConfig(
                display_name=c["display_name"],
                source_type=c.get("source_type", "youtube"),
                external_id=c["external_id"],
                handle=c.get("handle"),
                url=c.get("url"),
                active=c.get("active", True),
                notes=c.get("notes"),
            )
        )
    return out


def get_adapter(source_type: str) -> SourceAdapter:
    if source_type == "youtube":
        return YouTubeAdapter()
    raise ValueError(f"No adapter registered for source_type={source_type!r}")


def ingest_creator(
    cfg: CreatorConfig,
    *,
    limit: int | None = 25,
    adapter: SourceAdapter | None = None,
) -> int:
    """Ingest recent docs for a single creator. Returns number of new documents persisted.

    Pass `adapter` explicitly to share state (e.g. the YouTubeAdapter's
    ip_blocked flag, Whisper backend) across multiple creators in one run.
    """
    if adapter is None:
        adapter = get_adapter(cfg.source_type)
    log.info(
        "ingest.creator.start",
        creator=cfg.display_name,
        source=cfg.source_type,
        channel=cfg.external_id,
    )

    with session_scope() as session:
        creator = session.scalar(select(Creator).where(Creator.display_name == cfg.display_name))
        if creator is None:
            creator = Creator(
                display_name=cfg.display_name,
                primary_source_type=cfg.source_type,
                notes=cfg.notes,
                active=cfg.active,
            )
            session.add(creator)
            session.flush()

        channel = session.scalar(
            select(SourceChannel).where(
                SourceChannel.source_type == cfg.source_type,
                SourceChannel.external_id == cfg.external_id,
            )
        )
        if channel is None:
            channel = SourceChannel(
                creator_id=creator.id,
                source_type=cfg.source_type,
                external_id=cfg.external_id,
                handle=cfg.handle,
                url=cfg.url,
            )
            session.add(channel)
            session.flush()

        channel_id = channel.id

    new_count = 0
    external_ids = list(adapter.list_recent_documents(cfg.external_id, limit=limit))
    log.info("ingest.list", creator=cfg.display_name, found=len(external_ids))

    for vid in external_ids:
        if _document_exists(cfg.source_type, vid):
            continue
        try:
            raw = adapter.fetch_document(vid)
        except YouTubeIpBlocked:
            # Hard stop: continuing would just hammer a blocked IP.
            log.error(
                "ingest.aborted.ip_blocked",
                creator=cfg.display_name,
                processed_so_far=new_count,
            )
            raise
        except Exception as e:  # pragma: no cover - network
            log.warning("ingest.fetch.error", external_id=vid, error=str(e))
            continue

        _persist_document(channel_id, raw)
        new_count += 1

    log.info("ingest.creator.done", creator=cfg.display_name, new=new_count)
    return new_count


def ingest_all(*, limit: int | None = 25) -> dict[str, int]:
    """Ingest all active creators. Returns mapping of creator -> new doc count.

    The same adapter instance is shared across all creators so that
    cross-creator state (e.g. YouTubeAdapter's ip_blocked flag, Whisper
    backend) persists for the whole run.

    Aborts the whole run only on YouTubeIpBlocked when no fallback is
    available — see docs/decisions/0004. With Whisper configured, IP-blocks
    are absorbed transparently and the run continues.
    """
    results: dict[str, int] = {}
    adapters: dict[str, SourceAdapter] = {}
    for cfg in load_creator_configs():
        if not cfg.active:
            continue
        if cfg.source_type not in adapters:
            adapters[cfg.source_type] = get_adapter(cfg.source_type)
        try:
            results[cfg.display_name] = ingest_creator(
                cfg, limit=limit, adapter=adapters[cfg.source_type]
            )
        except YouTubeIpBlocked:
            log.error("ingest.run.aborted.ip_blocked", remaining_skipped=True)
            results[cfg.display_name] = -1
            break
    return results


def _document_exists(source_type: str, external_id: str) -> bool:
    with session_scope() as session:
        existing = session.scalar(
            select(Document.id).where(
                Document.source_type == source_type,
                Document.external_id == external_id,
            )
        )
        return existing is not None


def _persist_document(channel_id: int, raw: RawDocument) -> None:
    with session_scope() as session:
        doc = Document(
            source_channel_id=channel_id,
            source_type=raw.source_type,
            external_id=raw.external_id,
            title=raw.title,
            description=raw.description,
            posted_at=_ensure_utc(raw.posted_at),
            url=raw.url,
            duration_seconds=raw.duration_seconds,
        )
        session.add(doc)
        session.flush()

        for seg in raw.segments:
            session.add(
                TranscriptSegment(
                    document_id=doc.id,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    text=seg.text,
                    speaker_label=seg.speaker_label,
                )
            )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        from datetime import UTC

        return dt.replace(tzinfo=UTC)
    return dt
