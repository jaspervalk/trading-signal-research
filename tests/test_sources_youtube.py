"""Unit tests for YouTubeAdapter transcript-fallback logic.

We mock the youtube-transcript-api and the Whisper backend so these tests
run offline and deterministically.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.sources.base import RawSegment
from app.sources.whisper_fallback import (
    AudioFile,
    WhisperBackend,
    WhisperUnavailable,
)
from app.sources.youtube import YouTubeAdapter, YouTubeIpBlocked


class _FakeWhisper(WhisperBackend):
    name = "fake"

    def __init__(self, segments: list[RawSegment] | None = None, raise_with: Exception | None = None):
        self._segs = segments or []
        self._raise = raise_with

    def transcribe(self, audio: AudioFile) -> list[RawSegment]:
        if self._raise:
            raise self._raise
        return list(self._segs)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Strip the per-fetch delay so tests don't take forever."""
    monkeypatch.setattr("app.sources.youtube.time.sleep", lambda _s: None)


@pytest.fixture
def fake_audio(monkeypatch):
    """Stub download_audio so no network is touched."""
    def _fake(video_id, format="m4a"):
        return AudioFile(path=f"/tmp/{video_id}.m4a", duration_seconds=60.0)  # type: ignore[arg-type]
    monkeypatch.setattr("app.sources.youtube.download_audio", _fake)


def test_native_transcript_success_skips_whisper(monkeypatch):
    """When the native API returns segments, Whisper is never called."""
    class _Snippet:
        def __init__(self, start, duration, text):
            self.start = start
            self.duration = duration
            self.text = text

    class _FakeAPI:
        def fetch(self, video_id, languages):
            return iter([_Snippet(0.0, 2.0, "hello"), _Snippet(2.0, 1.0, "world")])

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    adapter = YouTubeAdapter(whisper=_FakeWhisper(raise_with=AssertionError("should not be called")))
    segs = adapter._fetch_transcript_with_fallback("v1")
    assert [s.text for s in segs] == ["hello", "world"]


def test_native_missing_falls_back_to_whisper(monkeypatch, fake_audio):
    """No native transcript → Whisper is invoked and its segments returned."""
    from youtube_transcript_api import NoTranscriptFound

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise NoTranscriptFound(video_id, languages, transcript_data=None)

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    fake = _FakeWhisper(segments=[RawSegment(0.0, 5.0, "from whisper")])
    adapter = YouTubeAdapter(whisper=fake)
    segs = adapter._fetch_transcript_with_fallback("v_missing")
    assert [s.text for s in segs] == ["from whisper"]


def test_ip_block_falls_back_to_whisper(monkeypatch, fake_audio):
    """IP-block → Whisper kicks in and ip_blocked flag is set on the adapter."""

    class _IpBlocked(Exception):
        pass

    _IpBlocked.__name__ = "IpBlocked"

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise _IpBlocked("blocked")

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    fake = _FakeWhisper(segments=[RawSegment(0.0, 5.0, "fallback worked")])
    adapter = YouTubeAdapter(whisper=fake)

    segs = adapter._fetch_transcript_with_fallback("v_blocked")
    assert [s.text for s in segs] == ["fallback worked"]
    assert adapter._ip_blocked is True


def test_ip_block_persists_skips_native_for_subsequent_videos(monkeypatch, fake_audio):
    """Once IP-blocked, subsequent videos go straight to Whisper without re-asking the API."""

    call_count = {"n": 0}

    class _IpBlocked(Exception):
        pass

    _IpBlocked.__name__ = "IpBlocked"

    class _FakeAPI:
        def fetch(self, video_id, languages):
            call_count["n"] += 1
            raise _IpBlocked("blocked")

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    adapter = YouTubeAdapter(whisper=_FakeWhisper(segments=[RawSegment(0.0, 1.0, "wh")]))
    adapter._fetch_transcript_with_fallback("v1")  # triggers ip_block
    adapter._fetch_transcript_with_fallback("v2")  # should skip native API
    adapter._fetch_transcript_with_fallback("v3")
    assert call_count["n"] == 1, "native API should be called only once before being skipped"


def test_ip_block_with_no_fallback_raises(monkeypatch):
    """When Whisper backend is unavailable AND IP is blocked, raise YouTubeIpBlocked."""

    class _IpBlocked(Exception):
        pass

    _IpBlocked.__name__ = "IpBlocked"

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise _IpBlocked("blocked")

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    def _no_backend():
        raise WhisperUnavailable("disabled")

    monkeypatch.setattr("app.sources.youtube.get_whisper_backend", _no_backend)

    adapter = YouTubeAdapter()  # no whisper passed; will lazy-init
    with pytest.raises(YouTubeIpBlocked):
        adapter._fetch_transcript_with_fallback("v1")


def test_native_missing_with_no_fallback_returns_empty(monkeypatch):
    """Missing native transcript + no Whisper → empty list, NOT a hard failure."""
    from youtube_transcript_api import NoTranscriptFound

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise NoTranscriptFound(video_id, languages, transcript_data=None)

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    def _no_backend():
        raise WhisperUnavailable("disabled")

    monkeypatch.setattr("app.sources.youtube.get_whisper_backend", _no_backend)

    adapter = YouTubeAdapter()
    segs = adapter._fetch_transcript_with_fallback("v1")
    assert segs == []


def test_whisper_failure_with_native_missing_returns_empty(monkeypatch, fake_audio):
    """Whisper raises but native already said 'no transcript' → empty, no exception."""
    from youtube_transcript_api import NoTranscriptFound

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise NoTranscriptFound(video_id, languages, transcript_data=None)

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    adapter = YouTubeAdapter(whisper=_FakeWhisper(raise_with=WhisperUnavailable("ffmpeg missing")))
    segs = adapter._fetch_transcript_with_fallback("v1")
    assert segs == []


def test_whisper_failure_with_ip_block_raises(monkeypatch, fake_audio):
    """Whisper fails AND IP is blocked → YouTubeIpBlocked (run aborts)."""
    class _IpBlocked(Exception):
        pass

    _IpBlocked.__name__ = "IpBlocked"

    class _FakeAPI:
        def fetch(self, video_id, languages):
            raise _IpBlocked("blocked")

    monkeypatch.setattr("app.sources.youtube.YouTubeTranscriptApi", lambda: _FakeAPI())

    adapter = YouTubeAdapter(whisper=_FakeWhisper(raise_with=WhisperUnavailable("oom")))
    with pytest.raises(YouTubeIpBlocked):
        adapter._fetch_transcript_with_fallback("v1")
