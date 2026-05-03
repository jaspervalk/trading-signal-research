"""Whisper-based transcript fallback for when youtube-transcript-api fails.

Two backends:
  - 'faster_whisper': local CTranslate2-backed Whisper. Free, no API key.
                       Heavy first-call (model download). CPU OK with int8.
  - 'openai_api':     OpenAI Whisper API. Fast, costs money. 25MB upload limit.

Audio is downloaded once via yt-dlp and cached under `data/cache/audio/`.
The cache survives across runs so re-running ingest with a different backend
doesn't re-download.
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, load_env, load_project_settings
from app.logging import get_logger
from app.sources.base import RawSegment

log = get_logger(__name__)


class WhisperUnavailable(RuntimeError):
    """Whisper backend not configured / library not installed / no API key."""


@dataclass
class AudioFile:
    path: Path
    duration_seconds: float | None


class WhisperBackend(ABC):
    """Pluggable backend for audio → transcript segments."""

    name: str

    @abstractmethod
    def transcribe(self, audio: AudioFile) -> list[RawSegment]: ...


class _NoopBackend(WhisperBackend):
    """No-op backend used when whisper_backend='none'. Always raises."""

    name = "none"

    def transcribe(self, audio: AudioFile) -> list[RawSegment]:
        raise WhisperUnavailable("whisper_backend='none'; fallback disabled")


class FasterWhisperBackend(WhisperBackend):
    """Local Whisper via faster-whisper. Lazy-loads the model on first use."""

    name = "faster_whisper"

    def __init__(self, *, model: str, device: str, compute_type: str) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as e:
            raise WhisperUnavailable(
                "faster-whisper not installed. Run: pip install -e \".[whisper]\""
            ) from e
        self._WhisperModel = WhisperModel
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            log.info(
                "whisper.local.loading",
                model=self._model_name,
                device=self._device,
                compute_type=self._compute_type,
            )
            self._model = self._WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio: AudioFile) -> list[RawSegment]:
        model = self._ensure_model()
        log.info("whisper.local.transcribe.start", path=str(audio.path))
        segments, _info = model.transcribe(str(audio.path), beam_size=5)
        out: list[RawSegment] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(
                RawSegment(
                    start_seconds=float(seg.start),
                    end_seconds=float(seg.end),
                    text=text,
                )
            )
        log.info("whisper.local.transcribe.done", path=str(audio.path), n_segments=len(out))
        return out


class OpenAIWhisperBackend(WhisperBackend):
    """OpenAI Whisper API. Requires OPENAI_API_KEY."""

    name = "openai_api"
    _MAX_BYTES = 25 * 1024 * 1024  # OpenAI upload limit

    def __init__(self) -> None:
        env = load_env()
        if not env.openai_api_key:
            raise WhisperUnavailable("OPENAI_API_KEY not set; cannot use openai_api backend")
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise WhisperUnavailable(
                "openai package not installed. Run: pip install -e \".[whisper]\""
            ) from e
        self._client = OpenAI(api_key=env.openai_api_key)

    def transcribe(self, audio: AudioFile) -> list[RawSegment]:
        size = audio.path.stat().st_size
        if size > self._MAX_BYTES:
            raise WhisperUnavailable(
                f"Audio file {size/1e6:.1f}MB exceeds OpenAI 25MB limit. "
                "Use faster_whisper backend or chunk the audio."
            )
        log.info("whisper.openai.transcribe.start", path=str(audio.path), size_mb=size / 1e6)
        with audio.path.open("rb") as f:
            resp = self._client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        out: list[RawSegment] = []
        for seg in getattr(resp, "segments", None) or []:
            text = (_attr(seg, "text") or "").strip()
            if not text:
                continue
            start = _attr(seg, "start")
            end = _attr(seg, "end")
            if start is None or end is None:
                continue
            out.append(
                RawSegment(start_seconds=float(start), end_seconds=float(end), text=text)
            )
        log.info("whisper.openai.transcribe.done", path=str(audio.path), n_segments=len(out))
        return out


def _attr(obj: Any, name: str) -> Any:
    """Read `name` from an object or a dict — OpenAI SDK returns either."""
    val = getattr(obj, name, None)
    if val is not None:
        return val
    if isinstance(obj, dict):
        return obj.get(name)
    return None


def get_whisper_backend() -> WhisperBackend:
    """Build the configured backend. Raises WhisperUnavailable if disabled/broken."""
    s = load_project_settings().transcript
    backend_name = s.whisper_backend.lower().strip()
    if backend_name == "none":
        return _NoopBackend()
    if backend_name == "faster_whisper":
        return FasterWhisperBackend(
            model=s.whisper_model,
            device=s.whisper_device,
            compute_type=s.whisper_compute_type,
        )
    if backend_name == "openai_api":
        return OpenAIWhisperBackend()
    raise WhisperUnavailable(f"Unknown whisper_backend: {backend_name!r}")


# --- Audio download via yt-dlp ---------------------------------------------


def _audio_cache_dir() -> Path:
    s = load_project_settings().transcript
    p = Path(s.audio_cache_dir)
    if not p.is_absolute():
        p = REPO_ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def download_audio(video_id: str, *, format: str = "m4a") -> AudioFile:
    """Download audio for a YouTube video. Cached under data/cache/audio.

    Requires `ffmpeg` on PATH for codec conversion.
    """
    if shutil.which("ffmpeg") is None:
        raise WhisperUnavailable(
            "ffmpeg not found on PATH. Install via `brew install ffmpeg` "
            "(macOS) or your package manager."
        )

    cache_dir = _audio_cache_dir()
    out_path = cache_dir / f"{video_id}.{format}"
    if out_path.exists():
        log.debug("whisper.audio.cache_hit", video_id=video_id)
        return AudioFile(path=out_path, duration_seconds=None)

    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(cache_dir / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": format,
                "preferredquality": "128",
            }
        ],
    }
    log.info("whisper.audio.download", video_id=video_id)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)

    if not out_path.exists():
        # postprocessor may have produced a different extension; find by id.
        candidates = list(cache_dir.glob(f"{video_id}.*"))
        if not candidates:
            raise WhisperUnavailable(f"Audio download succeeded but file not found for {video_id}")
        out_path = candidates[0]

    duration = float(info.get("duration", 0.0)) if isinstance(info, dict) else None
    return AudioFile(path=out_path, duration_seconds=duration)


def cleanup_audio(video_id: str) -> None:
    """Remove cached audio for a single video. Safe to call when missing."""
    cache_dir = _audio_cache_dir()
    for p in cache_dir.glob(f"{video_id}.*"):
        try:
            os.unlink(p)
        except OSError:
            pass
