"""YouTube source adapter.

Layered strategy:
  1. Metadata + listing:
       - if YOUTUBE_API_KEY is set: official YouTube Data API v3
       - else: yt-dlp scraping (no key needed; less reliable upload dates)
  2. Transcripts:
       - youtube-transcript-api (no key, but IP-blocks aggressively)
       - on IpBlocked / no-captions: Whisper fallback (faster-whisper or OpenAI)
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import yt_dlp
from tenacity import retry, stop_after_attempt, wait_exponential
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.config import load_env, load_project_settings
from app.logging import get_logger
from app.sources.base import RawDocument, RawSegment, SourceAdapter
from app.sources.whisper_fallback import (
    WhisperBackend,
    WhisperUnavailable,
    download_audio,
    get_whisper_backend,
)
from app.sources.youtube_api import (
    APIVideoMeta,
    YouTubeAPIError,
    YouTubeDataAPIClient,
)

log = get_logger(__name__)


class YouTubeIpBlocked(RuntimeError):
    """Raised when YouTube has blocked our IP from the transcript endpoint AND
    the configured Whisper fallback is unable to recover (disabled, missing
    deps, missing ffmpeg, missing API key, etc.).

    When the fallback succeeds, the IP-block is silently absorbed and the run
    continues with the Whisper-produced segments.
    """


class YouTubeAdapter(SourceAdapter):
    source_type = "youtube"

    _LIST_OPTS = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }

    _META_OPTS = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }

    def __init__(
        self,
        *,
        whisper: WhisperBackend | None = None,
        api_client: YouTubeDataAPIClient | None = None,
    ) -> None:
        # Lazy-build the Whisper backend on first use, not at construction —
        # this keeps tests and Phase-1a-only flows from paying its import cost.
        self._whisper = whisper
        self._whisper_attempted = whisper is not None
        self._whisper_disabled = False  # set True when first attempt fails fatally
        self._ip_blocked = False        # once true, skip native API for the run

        # Optional: official Data API client. If absent and YOUTUBE_API_KEY is
        # set, we'll lazy-construct one. If unset, we use yt-dlp.
        self._api_client = api_client
        self._api_client_attempted = api_client is not None
        # Per-video metadata cache populated during list_recent_documents so
        # fetch_document can read from it instead of round-tripping the API.
        self._meta_cache: dict[str, APIVideoMeta] = {}

    # ---- listing & per-video orchestration ----------------------------

    def list_recent_documents(
        self,
        channel_external_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Iterable[str]:
        """List recent video IDs for a channel.

        Uses the Data API when available (reliable upload dates → real `since`
        filtering). Falls back to yt-dlp scraping when no API key is set.
        """
        api = self._maybe_api_client()
        if api is not None:
            try:
                channel_id = api.resolve_channel_id(channel_external_id)
                uploads = api.get_uploads_playlist_id(channel_id)
                video_ids = list(api.list_uploads_video_ids(uploads, since=since, limit=limit))
                if video_ids:
                    # Pre-fetch metadata in batches of 50 so per-video lookups are free.
                    metas = api.get_videos_metadata(video_ids)
                    self._meta_cache.update({m.video_id: m for m in metas})
                log.info(
                    "yt.list.via_api",
                    channel=channel_external_id,
                    found=len(video_ids),
                )
                return video_ids
            except YouTubeAPIError as e:
                log.warning(
                    "yt.list.api_failed_falling_back",
                    channel=channel_external_id,
                    error=str(e),
                )
                # Fall through to yt-dlp.

        return self._list_recent_via_ytdlp(channel_external_id, since=since, limit=limit)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def fetch_document(self, external_id: str) -> RawDocument:
        """Fetch metadata + transcript (with Whisper fallback) for a single video."""
        self._sleep_between_fetches()

        # Prefer cached Data API metadata; fall back to yt-dlp per-video lookup.
        cached = self._meta_cache.get(external_id)
        if cached is not None:
            posted_at = cached.posted_at
            title = cached.title
            description = cached.description
            url = cached.url
            channel_id = cached.channel_id
            duration_seconds = cached.duration_seconds
        else:
            url = f"https://www.youtube.com/watch?v={external_id}"
            with yt_dlp.YoutubeDL(self._META_OPTS) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            posted_at = self._parse_upload_date(info)
            title = info.get("title")
            description = info.get("description")
            channel_id = info.get("channel_id") or info.get("uploader_id") or ""
            duration_seconds = int(info["duration"]) if info.get("duration") else None
            url = info.get("webpage_url") or url

        segments = self._fetch_transcript_with_fallback(external_id)

        return RawDocument(
            source_type=self.source_type,
            external_id=external_id,
            channel_external_id=channel_id,
            title=title,
            description=description,
            posted_at=posted_at,
            url=url,
            duration_seconds=duration_seconds,
            segments=segments,
        )

    # ---- yt-dlp listing (fallback path) -------------------------------

    def _list_recent_via_ytdlp(
        self,
        channel_external_id: str,
        *,
        since: datetime | None,
        limit: int | None,
    ) -> list[str]:
        url = self._channel_videos_url(channel_external_id)
        opts = {**self._LIST_OPTS}
        if limit is not None:
            opts["playlistend"] = limit

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        entries = (info or {}).get("entries") or []
        ids: list[str] = []
        for e in entries:
            if not e:
                continue
            video_id = e.get("id")
            if not video_id:
                continue
            ids.append(video_id)

        if since is not None:
            log.debug(
                "yt.list.since_filter_deferred_ytdlp",
                channel=channel_external_id,
                note="extract_flat lacks upload_date; filter applied per-video",
            )
        return ids

    # ---- Data API client lazy init ------------------------------------

    def _maybe_api_client(self) -> YouTubeDataAPIClient | None:
        if self._api_client is not None:
            return self._api_client
        if self._api_client_attempted:
            return None  # we tried before and there was no key
        self._api_client_attempted = True
        key = load_env().youtube_api_key
        if not key:
            log.debug("yt.api.no_key_using_ytdlp")
            return None
        self._api_client = YouTubeDataAPIClient(key)
        log.info("yt.api.client_ready")
        return self._api_client

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _channel_videos_url(channel_external_id: str) -> str:
        cid = channel_external_id.strip()
        if cid.startswith("@"):
            return f"https://www.youtube.com/{cid}/videos"
        if cid.startswith("UC"):
            return f"https://www.youtube.com/channel/{cid}/videos"
        return f"https://www.youtube.com/@{cid}/videos"

    @staticmethod
    def _parse_upload_date(info: dict) -> datetime:  # type: ignore[type-arg]
        ts = info.get("timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=UTC)
        ud = info.get("upload_date")
        if ud:
            return datetime.strptime(ud, "%Y%m%d").replace(tzinfo=UTC) + timedelta(hours=12)
        log.warning("yt.fetch.no_upload_date", external_id=info.get("id"))
        return datetime.now(tz=UTC)

    @staticmethod
    def _sleep_between_fetches() -> None:
        s = load_project_settings().transcript
        delay = max(0.0, s.fetch_delay_seconds)
        if s.fetch_jitter_seconds > 0:
            delay += random.uniform(0, s.fetch_jitter_seconds)
        if delay > 0:
            time.sleep(delay)

    # ---- transcript fetch + fallback ----------------------------------

    def _fetch_transcript_with_fallback(self, video_id: str) -> list[RawSegment]:
        """Try the native transcript API; on IP-block or no-transcript, try Whisper."""
        # If we've already been IP-blocked this run, skip straight to Whisper.
        native_segments: list[RawSegment] | None = None
        ip_blocked_now = False
        no_native_transcript = False

        if not self._ip_blocked:
            native_segments, ip_blocked_now = self._fetch_native_transcript(video_id)
            if ip_blocked_now:
                self._ip_blocked = True
            no_native_transcript = native_segments is not None and not native_segments

        if native_segments and not ip_blocked_now:
            return native_segments

        # Need fallback. Two cases land here:
        #   (a) IP-blocked on this video (or earlier) → native_segments is [].
        #   (b) Native API said "no transcript available" → native_segments is [].
        try:
            backend = self._ensure_whisper_backend()
        except WhisperUnavailable as e:
            if ip_blocked_now or self._ip_blocked:
                log.error(
                    "yt.transcript.no_fallback_for_ip_block",
                    video_id=video_id,
                    reason=str(e),
                )
                raise YouTubeIpBlocked(str(e)) from e
            log.info(
                "yt.transcript.no_fallback_for_missing",
                video_id=video_id,
                reason=str(e),
            )
            return []

        try:
            return self._whisper_fetch(backend, video_id)
        except WhisperUnavailable as e:
            log.warning("yt.transcript.whisper_failed", video_id=video_id, error=str(e))
            if ip_blocked_now or self._ip_blocked:
                raise YouTubeIpBlocked(str(e)) from e
            return []

    def _fetch_native_transcript(
        self, video_id: str
    ) -> tuple[list[RawSegment] | None, bool]:
        """Try youtube-transcript-api. Returns (segments_or_None, ip_blocked).

        - segments=[]   → endpoint reachable but no transcript available
        - segments=[...]→ success
        - segments=None → unrecoverable error other than IpBlocked
        - ip_blocked=True signals: stop using native API for the rest of the run.
        """
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except (TranscriptsDisabled, NoTranscriptFound):
            log.info("yt.transcript.missing", video_id=video_id)
            return [], False
        except Exception as e:  # pragma: no cover
            ename = type(e).__name__
            if ename in {"IpBlocked", "RequestBlocked", "YouTubeRequestFailed"}:
                log.warning(
                    "yt.transcript.ip_blocked",
                    video_id=video_id,
                    exception=ename,
                    hint="switching to Whisper fallback for this video and the rest of the run",
                )
                return [], True
            log.warning("yt.transcript.error", video_id=video_id, error=str(e))
            return None, False

        out: list[RawSegment] = []
        for snippet in fetched:
            text = (snippet.text or "").strip()
            if not text:
                continue
            start = float(snippet.start)
            duration = float(getattr(snippet, "duration", 0.0) or 0.0)
            out.append(RawSegment(start_seconds=start, end_seconds=start + duration, text=text))
        return out, False

    def _ensure_whisper_backend(self) -> WhisperBackend:
        if self._whisper_disabled:
            raise WhisperUnavailable("whisper backend previously failed to initialise")
        if self._whisper is None:
            self._whisper = get_whisper_backend()
            self._whisper_attempted = True
            log.info("yt.whisper.backend_ready", backend=self._whisper.name)
        return self._whisper

    def _whisper_fetch(self, backend: WhisperBackend, video_id: str) -> list[RawSegment]:
        audio = download_audio(video_id)
        segments = backend.transcribe(audio)
        return segments
