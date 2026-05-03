"""Source adapters. New sources implement SourceAdapter and slot in here."""

from app.sources.base import RawDocument, RawSegment, SourceAdapter
from app.sources.youtube import YouTubeAdapter

__all__ = ["RawDocument", "RawSegment", "SourceAdapter", "YouTubeAdapter"]
