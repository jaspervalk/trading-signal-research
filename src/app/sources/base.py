"""SourceAdapter abstract interface.

Every content source (YouTube, future Discord) implements this.
Downstream stages (normalization, extraction, etc.) only ever see RawDocument /
RawSegment, never source-specific types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RawSegment:
    """A timestamped chunk of text from a source document."""

    start_seconds: float
    end_seconds: float
    text: str
    speaker_label: str | None = None


@dataclass(frozen=True)
class RawDocument:
    """A single unit of content fetched from a source.

    For YouTube: one video. For Discord (future): one message or message-thread.

    `external_id` is the source-native identifier (video ID, message ID).
    `(source_type, external_id)` is unique across the database.
    """

    source_type: str
    external_id: str
    channel_external_id: str
    title: str | None
    description: str | None
    posted_at: datetime  # tz-aware UTC
    url: str | None
    duration_seconds: int | None
    segments: list[RawSegment] = field(default_factory=list)


class SourceAdapter(ABC):
    """A pluggable content-source adapter."""

    source_type: str

    @abstractmethod
    def list_recent_documents(
        self, channel_external_id: str, *, since: datetime | None = None, limit: int | None = None
    ) -> Iterable[str]:
        """Return external IDs of recent documents on this channel."""

    @abstractmethod
    def fetch_document(self, external_id: str) -> RawDocument:
        """Fetch a single document by external ID, including segments."""

    def fetch_documents(self, external_ids: Iterable[str]) -> Iterator[RawDocument]:
        """Default impl: fetch sequentially. Adapters may override for batching."""
        for eid in external_ids:
            yield self.fetch_document(eid)
