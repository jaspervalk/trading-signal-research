"""Tests for YouTubeDataAPIClient — mocked HTTP."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from app.sources.youtube_api import (
    YouTubeAPIError,
    YouTubeAPIQuotaExceeded,
    YouTubeDataAPIClient,
    _parse_iso8601_duration,
)


def _client_with(*responses: tuple[int, dict] | tuple[int, dict, dict]) -> tuple[YouTubeDataAPIClient, MagicMock]:
    """Build a client whose httpx.Client returns the given (status, json_body) sequence."""
    iter_ = iter(responses)

    def _fake_get(url: str, params: dict | None = None) -> httpx.Response:
        status, body = next(iter_)
        return httpx.Response(status_code=status, json=body, request=httpx.Request("GET", url))

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = _fake_get
    return YouTubeDataAPIClient("FAKE_KEY", client=mock_client), mock_client


def test_resolve_channel_id_uc_passthrough():
    c, mock = _client_with()
    assert c.resolve_channel_id("UCabc123") == "UCabc123"
    mock.get.assert_not_called()


def test_resolve_channel_id_handle():
    c, mock = _client_with((200, {"items": [{"id": "UCxyz"}]}))
    assert c.resolve_channel_id("@traderisk") == "UCxyz"
    mock.get.assert_called_once()
    call = mock.get.call_args
    assert call.kwargs["params"]["forHandle"] == "@traderisk"


def test_resolve_channel_id_not_found():
    c, _ = _client_with((200, {"items": []}))
    with pytest.raises(YouTubeAPIError, match="handle not found"):
        c.resolve_channel_id("@ghost")


def test_get_uploads_playlist_id():
    c, _ = _client_with(
        (200, {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]})
    )
    assert c.get_uploads_playlist_id("UCabc") == "UU123"


def test_list_uploads_video_ids_paginates():
    c, _ = _client_with(
        (
            200,
            {
                "items": [
                    {"contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-05-01T10:00:00Z"}},
                    {"contentDetails": {"videoId": "v2", "videoPublishedAt": "2026-04-30T10:00:00Z"}},
                ],
                "nextPageToken": "TOKEN2",
            },
        ),
        (
            200,
            {
                "items": [
                    {"contentDetails": {"videoId": "v3", "videoPublishedAt": "2026-04-29T10:00:00Z"}},
                ]
            },
        ),
    )
    out = list(c.list_uploads_video_ids("UU123"))
    assert out == ["v1", "v2", "v3"]


def test_list_uploads_video_ids_respects_limit():
    c, _ = _client_with(
        (
            200,
            {
                "items": [
                    {"contentDetails": {"videoId": f"v{i}", "videoPublishedAt": "2026-05-01T10:00:00Z"}}
                    for i in range(10)
                ]
            },
        )
    )
    out = list(c.list_uploads_video_ids("UU123", limit=3))
    assert out == ["v0", "v1", "v2"]


def test_list_uploads_video_ids_short_circuits_on_since():
    c, _ = _client_with(
        (
            200,
            {
                "items": [
                    {"contentDetails": {"videoId": "v1", "videoPublishedAt": "2026-05-01T10:00:00Z"}},
                    {"contentDetails": {"videoId": "v2", "videoPublishedAt": "2026-04-30T10:00:00Z"}},
                    {"contentDetails": {"videoId": "v3", "videoPublishedAt": "2026-04-01T10:00:00Z"}},
                ]
            },
        )
    )
    since = datetime(2026, 4, 15, tzinfo=UTC)
    out = list(c.list_uploads_video_ids("UU123", since=since))
    assert out == ["v1", "v2"]


def test_get_videos_metadata():
    c, _ = _client_with(
        (
            200,
            {
                "items": [
                    {
                        "id": "v1",
                        "snippet": {
                            "channelId": "UCabc",
                            "title": "Stock Watchlist",
                            "description": "weekly setups",
                            "publishedAt": "2026-05-01T14:30:00Z",
                        },
                        "contentDetails": {"duration": "PT12M34S"},
                    }
                ]
            },
        )
    )
    metas = c.get_videos_metadata(["v1"])
    assert len(metas) == 1
    m = metas[0]
    assert m.video_id == "v1"
    assert m.title == "Stock Watchlist"
    assert m.posted_at == datetime(2026, 5, 1, 14, 30, tzinfo=UTC)
    assert m.duration_seconds == 12 * 60 + 34
    assert m.url == "https://www.youtube.com/watch?v=v1"


def test_get_videos_metadata_batches_50():
    """Verify that >50 ids triggers multiple API calls."""
    items_page1 = {"items": [{"id": f"v{i}", "snippet": {"channelId": "c", "title": None, "description": None, "publishedAt": "2026-05-01T00:00:00Z"}, "contentDetails": {"duration": "PT1M"}} for i in range(50)]}
    items_page2 = {"items": [{"id": f"v{i}", "snippet": {"channelId": "c", "title": None, "description": None, "publishedAt": "2026-05-01T00:00:00Z"}, "contentDetails": {"duration": "PT1M"}} for i in range(50, 60)]}
    c, mock = _client_with((200, items_page1), (200, items_page2))
    out = c.get_videos_metadata([f"v{i}" for i in range(60)])
    assert len(out) == 60
    assert mock.get.call_count == 2


def test_quota_exceeded_raises_specific_error():
    c, _ = _client_with(
        (403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
    )
    with pytest.raises(YouTubeAPIQuotaExceeded):
        c.resolve_channel_id("@x")


def test_other_4xx_raises_generic_error():
    c, _ = _client_with(
        (400, {"error": {"errors": [{"reason": "badRequest"}]}})
    )
    with pytest.raises(YouTubeAPIError, match="badRequest"):
        c.resolve_channel_id("@x")


def test_iso8601_duration_parse():
    assert _parse_iso8601_duration("PT0S") == 0
    assert _parse_iso8601_duration("PT34S") == 34
    assert _parse_iso8601_duration("PT12M34S") == 12 * 60 + 34
    assert _parse_iso8601_duration("PT1H23M45S") == 3600 + 23 * 60 + 45
    assert _parse_iso8601_duration(None) is None
    assert _parse_iso8601_duration("garbage") is None
