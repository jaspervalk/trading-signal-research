"""Test that SourceAdapter contract behaves as expected with a fake adapter.

Validates the abstraction without hitting YouTube.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.sources.base import RawDocument, RawSegment, SourceAdapter


class FakeAdapter(SourceAdapter):
    source_type = "fake"

    def __init__(self, docs: dict[str, RawDocument]):
        self._docs = docs

    def list_recent_documents(
        self, channel_external_id: str, *, since=None, limit=None
    ) -> Iterable[str]:
        return list(self._docs)

    def fetch_document(self, external_id: str) -> RawDocument:
        return self._docs[external_id]


def test_fake_adapter_yields_documents():
    docs = {
        "doc1": RawDocument(
            source_type="fake",
            external_id="doc1",
            channel_external_id="chan",
            title="t",
            description="d",
            posted_at=datetime(2026, 5, 1, tzinfo=UTC),
            url=None,
            duration_seconds=None,
            segments=[RawSegment(start_seconds=0.0, end_seconds=1.0, text="hello")],
        )
    }
    a = FakeAdapter(docs)

    ids = list(a.list_recent_documents("chan"))
    assert ids == ["doc1"]

    doc = a.fetch_document("doc1")
    assert doc.title == "t"
    assert doc.segments[0].text == "hello"


def test_fetch_documents_default_iterates():
    docs = {
        f"d{i}": RawDocument(
            source_type="fake",
            external_id=f"d{i}",
            channel_external_id="chan",
            title=None,
            description=None,
            posted_at=datetime(2026, 5, 1, tzinfo=UTC),
            url=None,
            duration_seconds=None,
        )
        for i in range(3)
    }
    a = FakeAdapter(docs)
    out = list(a.fetch_documents(["d0", "d2"]))
    assert [d.external_id for d in out] == ["d0", "d2"]
