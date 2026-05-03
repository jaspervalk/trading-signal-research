"""Tests for whisper_fallback factory + backend selection.

We don't actually run Whisper — we verify the wiring (which backend gets
selected, what error messages surface) using monkeypatching.
"""

from __future__ import annotations

import pytest

from app.sources import whisper_fallback as wf


def test_get_backend_none(monkeypatch):
    """`whisper_backend='none'` returns a noop backend that always raises."""
    monkeypatch.setattr(
        "app.sources.whisper_fallback.load_project_settings",
        lambda: _settings_with(backend="none"),
    )
    b = wf.get_whisper_backend()
    with pytest.raises(wf.WhisperUnavailable):
        b.transcribe(wf.AudioFile(path="/tmp/x.m4a", duration_seconds=None))  # type: ignore[arg-type]


def test_get_backend_unknown_raises(monkeypatch):
    monkeypatch.setattr(
        "app.sources.whisper_fallback.load_project_settings",
        lambda: _settings_with(backend="alien"),
    )
    with pytest.raises(wf.WhisperUnavailable):
        wf.get_whisper_backend()


def test_openai_backend_requires_key(monkeypatch):
    """openai_api backend without OPENAI_API_KEY raises a clear error."""
    monkeypatch.setattr(
        "app.sources.whisper_fallback.load_project_settings",
        lambda: _settings_with(backend="openai_api"),
    )
    monkeypatch.setattr(
        "app.sources.whisper_fallback.load_env",
        lambda: type("E", (), {"openai_api_key": ""})(),
    )
    with pytest.raises(wf.WhisperUnavailable, match="OPENAI_API_KEY"):
        wf.get_whisper_backend()


def test_audio_cache_dir_created(monkeypatch, tmp_path):
    """audio cache dir is auto-created relative to repo root."""
    monkeypatch.setattr(
        "app.sources.whisper_fallback.load_project_settings",
        lambda: _settings_with(backend="none", audio_cache_dir=str(tmp_path / "audio")),
    )
    p = wf._audio_cache_dir()
    assert p.exists() and p.is_dir()


# ---- helpers --------------------------------------------------------


class _T:
    def __init__(self, **kw):
        self.whisper_backend = kw.get("backend", "none")
        self.whisper_model = kw.get("model", "base.en")
        self.whisper_device = kw.get("device", "cpu")
        self.whisper_compute_type = kw.get("compute_type", "int8")
        self.audio_cache_dir = kw.get("audio_cache_dir", "data/cache/audio")
        self.fetch_delay_seconds = 0.0
        self.fetch_jitter_seconds = 0.0


class _S:
    def __init__(self, **kw):
        self.transcript = _T(**kw)


def _settings_with(**kw):
    return _S(**kw)
