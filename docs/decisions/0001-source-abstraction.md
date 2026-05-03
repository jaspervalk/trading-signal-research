# ADR 0001 — Source abstraction

**Status:** accepted
**Date:** 2026-05-03

## Context

The system must ingest from YouTube in V1 and Discord in V2 (Phase 7), and we'd like to leave the door open for X/Substack/Reddit later. We do not want extraction, market enrichment, backtest, scoring, or modelling to know anything about the underlying source. If they do, adding Discord later means rewriting downstream code, not just adding an adapter.

## Decision

Define one abstract interface, `SourceAdapter`, with two normalized output types: `RawDocument` and `RawSegment`. All downstream stages consume only those types.

```python
class SourceAdapter(ABC):
    source_type: str

    def list_recent_documents(self, channel_external_id, *, since=None, limit=None) -> Iterable[str]: ...
    def fetch_document(self, external_id) -> RawDocument: ...
```

In the database, sources are discriminated by `(source_type, external_id)` natural keys on both `SourceChannel` and `Document`. There is no `youtube_*` or `discord_*` column anywhere downstream.

## Discord mapping (planned)

- A "channel" maps to a Discord guild + channel pair, encoded as one `external_id`.
- A "document" maps to either (a) one message-thread, or (b) a daily message-batch from a single user, depending on signal density. We will pick after surveying real traffic.
- A "segment" maps to one message.

## Consequences

- Adding a new source = implement the adapter, add a `get_adapter` branch in `ingest/run.py`, add fixtures. No downstream changes.
- Per-source quirks (e.g. YouTube transcript availability, Discord rate limits) live entirely inside the adapter.
- We pay a small upfront cost in `RawDocument` having generic field names (`title`, `url`, `posted_at`) instead of source-specific ones. Worth it.
