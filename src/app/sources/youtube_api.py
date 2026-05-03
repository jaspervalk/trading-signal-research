"""Thin wrapper around YouTube Data API v3.

Used as the primary path for channel listing + video metadata when
YOUTUBE_API_KEY is set. Falls back to yt-dlp when the key is missing.

Quota economics (default daily quota 10,000 units):
  - channels.list (forHandle):       1 unit
  - playlistItems.list (page of 50): 1 unit
  - videos.list (batch of 50 ids):   1 unit
  - search.list:                   100 units  ← we deliberately avoid this

A typical run for 8 creators × 25 recent videos ≈ 20 units total.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.logging import get_logger

log = get_logger(__name__)

_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(RuntimeError):
    """Raised on quota exhaustion / forbidden / 4xx-5xx that retries can't fix."""


class YouTubeAPIQuotaExceeded(YouTubeAPIError):
    """Quota exhausted for the day."""


@dataclass
class APIVideoMeta:
    """Subset of `videos.list` response we actually consume."""

    video_id: str
    channel_id: str
    title: str | None
    description: str | None
    posted_at: datetime  # tz-aware UTC
    duration_seconds: int | None
    url: str


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, YouTubeAPIError)
        and not isinstance(exc, YouTubeAPIQuotaExceeded)
        and "transient" in str(exc).lower()
    )


class YouTubeDataAPIClient:
    """Light synchronous client over `youtube/v3` using httpx."""

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._key = api_key
        self._client = client or httpx.Client(timeout=30.0)

    # --------------------------- public API ---------------------------

    def resolve_channel_id(self, handle_or_id: str) -> str:
        """Map an @handle (or UC... id) to a canonical channel id."""
        cid = handle_or_id.strip()
        if cid.startswith("UC"):
            return cid
        handle = cid.lstrip("@")
        data = self._get(
            "channels",
            params={
                "part": "id",
                "forHandle": f"@{handle}",
                "maxResults": 1,
            },
        )
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"channel handle not found: {handle_or_id!r}")
        return items[0]["id"]

    def get_uploads_playlist_id(self, channel_id: str) -> str:
        """Each channel has an `uploads` playlist that lists all its videos."""
        data = self._get(
            "channels",
            params={"part": "contentDetails", "id": channel_id, "maxResults": 1},
        )
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(f"channel not found: {channel_id!r}")
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def list_uploads_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Iterator[str]:
        """Yield video ids from the uploads playlist, newest first.

        Stops when:
          - `limit` ids yielded
          - a video older than `since` is encountered (uploads list is
            ordered most-recent-first, so we can short-circuit)
        """
        page_token: str | None = None
        n_yielded = 0
        while True:
            params: dict[str, Any] = {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("playlistItems", params=params)

            for item in data.get("items") or []:
                cd = item.get("contentDetails") or {}
                video_id = cd.get("videoId")
                if not video_id:
                    continue
                if since is not None:
                    pub = cd.get("videoPublishedAt")
                    if pub:
                        ts = _parse_iso8601(pub)
                        if ts < since:
                            return
                yield video_id
                n_yielded += 1
                if limit is not None and n_yielded >= limit:
                    return

            page_token = data.get("nextPageToken")
            if not page_token:
                return

    def get_videos_metadata(self, video_ids: list[str]) -> list[APIVideoMeta]:
        """Batch fetch video metadata. Up to 50 ids per request."""
        out: list[APIVideoMeta] = []
        for chunk in _chunked(video_ids, 50):
            data = self._get(
                "videos",
                params={
                    "part": "snippet,contentDetails",
                    "id": ",".join(chunk),
                    "maxResults": 50,
                },
            )
            for item in data.get("items") or []:
                snippet = item.get("snippet") or {}
                cd = item.get("contentDetails") or {}
                vid = item["id"]
                out.append(
                    APIVideoMeta(
                        video_id=vid,
                        channel_id=snippet.get("channelId") or "",
                        title=snippet.get("title"),
                        description=snippet.get("description"),
                        posted_at=_parse_iso8601(snippet["publishedAt"]),
                        duration_seconds=_parse_iso8601_duration(cd.get("duration")),
                        url=f"https://www.youtube.com/watch?v={vid}",
                    )
                )
        return out

    # --------------------------- internals ---------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "key": self._key}
        url = f"{_BASE}/{path}"
        try:
            resp = self._client.get(url, params=params)
        except httpx.TransportError:
            raise

        if resp.status_code == 200:
            return resp.json()  # type: ignore[no-any-return]

        # Surface quota errors specifically; everything else is generic.
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            pass
        reason = ""
        if isinstance(body, dict):
            errors = body.get("error", {}).get("errors") or []
            if errors:
                reason = errors[0].get("reason", "")

        if resp.status_code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded"}:
            raise YouTubeAPIQuotaExceeded(
                f"YouTube Data API quota exceeded ({reason}). "
                "Default daily quota is 10,000 units; resets at midnight Pacific."
            )
        if resp.status_code in _RETRYABLE_STATUSES:
            raise YouTubeAPIError(
                f"transient API error {resp.status_code}: {resp.text[:200]}"
            )
        raise YouTubeAPIError(
            f"API call failed: {path} status={resp.status_code} reason={reason!r} body={resp.text[:200]}"
        )


# ---------------- module-level helpers ----------------


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _parse_iso8601(value: str) -> datetime:
    # YouTube returns 'YYYY-MM-DDTHH:MM:SSZ' (sometimes with fractional seconds).
    s = value.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _parse_iso8601_duration(value: str | None) -> int | None:
    """Parse ISO 8601 duration like 'PT1H23M45S' → total seconds.

    YouTube uses a restricted subset (no days/months/years for video durations).
    """
    if not value or not value.startswith("PT"):
        return None
    body = value[2:]
    h = m = s = 0
    cur = ""
    for ch in body:
        if ch.isdigit():
            cur += ch
        elif ch == "H":
            h = int(cur or "0")
            cur = ""
        elif ch == "M":
            m = int(cur or "0")
            cur = ""
        elif ch == "S":
            s = int(cur or "0")
            cur = ""
        else:
            cur = ""
    return h * 3600 + m * 60 + s
