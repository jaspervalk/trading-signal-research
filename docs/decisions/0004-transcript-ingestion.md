# ADR 0004 — Transcript ingestion strategy + IP-block handling

**Status:** accepted (V1 baseline; will evolve)
**Date:** 2026-05-03

## Context

Phase 1a uses `youtube-transcript-api` to fetch auto-generated and human-uploaded transcripts directly from YouTube's transcript endpoint. This is the cheapest, fastest path: no API key, no transcoding, no Whisper compute.

The price: YouTube actively rate-limits and IP-blocks this endpoint. In a backfill of 8 creators × 25 videos (~175 transcript fetches in ~2 minutes), the second creator's transcripts succeed and from the third creator onwards the endpoint returns `IpBlocked`. The library exposes this as a distinct exception type — we catch it and **fail loud rather than silent**.

## Observed evidence

Initial backfill (2026-05-03):

| Creator | Docs | Transcripts |
|---|---|---|
| Adam Mancini | 5 | OK |
| The Trade Risk | 25 | OK |
| TraderLion | 25 | blocked |
| Alphatrends | 25 | blocked |
| Bulls on Wall Street | 25 | blocked |
| Qullamaggie | 25 | blocked |
| Oliver Velez Trading | 25 | blocked |

Manual probe a few minutes later confirmed `IpBlocked` was still active for the same residential IP.

## Decision

1. **Detect `IpBlocked` / `RequestBlocked` explicitly.** `YouTubeAdapter._fetch_transcript` distinguishes these from "transcript missing" and raises `YouTubeIpBlocked`.
2. **Hard-abort the ingest run on first IP-block hit.** Continuing pounds the same blocked IP and produces a misleading partial dataset where some creators look like they "have no transcripts" when in fact they were never asked.
3. **Document fix paths so the next operator (or the IDE you opened today) doesn't have to re-discover this.**

## Fix paths (in order of effort vs. robustness)

### A. Per-fetch delay + exponential backoff (cheap)

Add a 2–5 second delay between transcript fetches and a longer backoff (5–60 minutes) on the first IP-block. May or may not work depending on the IP's reputation. Easy to implement, easy to break.

### B. Residential proxy rotation (medium effort, robust)

Route transcript requests through a pool of residential IPs (Bright Data, Smartproxy, etc.). The `youtube-transcript-api` README documents this; pass a `proxies=` dict to the constructor. Costs money (~$10–20/month for low volume).

### C. Whisper fallback (medium effort, free, slow)

When the transcript endpoint fails or is missing, download the video's audio via `yt-dlp` (`--extract-audio`) and run local Whisper (or Whisper API) over it. Pros: bypasses YouTube's transcript API entirely; produces consistent quality across creators. Cons: GPU strongly preferred for local inference; ~30–90 seconds per 10-minute video; non-trivial disk usage during processing.

### D. YouTube Data API v3 + caption track download (high effort, official)

Use the official YouTube Data API to list caption tracks per video, then download each via the `captions.download` endpoint. Pros: official, generally won't IP-block. Cons: requires OAuth (not just an API key) for caption *downloads*; quota-limited; doesn't return auto-generated captions for videos owned by others.

**Note**: we DID adopt YouTube Data API v3 for **metadata + listing** (see [src/app/sources/youtube_api.py](../../src/app/sources/youtube_api.py)) since that part only needs an API key (no OAuth) and is the most rate-limited part of yt-dlp scraping. We deliberately did NOT pursue D for transcripts — the OAuth + ownership requirement makes it unworkable for tracking arbitrary creators.

## Recommendation for Phase 1b polish

Implement **(A) + (C)** as a layered strategy:
- Add a small per-fetch delay (configurable via `settings.yaml`) to slow the burst rate.
- On `YouTubeIpBlocked`, fall back to the Whisper path **for that video only** rather than aborting.

This keeps the architecture simple, removes the IP block as a hard blocker, and degrades gracefully (Whisper takes longer but always works).

## Status — implemented (2026-05-03)

Both layers shipped:

- **Per-fetch delay**: `transcript.fetch_delay_seconds` (default 2.5s) + `fetch_jitter_seconds` (default 1.0s) applied inside `YouTubeAdapter.fetch_document` before each video is fetched.
- **Whisper fallback**: pluggable `WhisperBackend` interface in [`src/app/sources/whisper_fallback.py`](../../src/app/sources/whisper_fallback.py) with two implementations:
  - `faster_whisper` (default) — local, free, requires `pip install -e ".[whisper]"` + `ffmpeg` on PATH.
  - `openai_api` — fast, cloud, requires `OPENAI_API_KEY` and respects the 25 MB upload limit.
  - `none` — explicitly disables fallback (preserves the old "abort on IP-block" behaviour).
- **Audio cache**: downloaded audio is stored under `data/cache/audio/` and reused across runs so re-running with a different backend doesn't re-download.

Behaviour after the fix:
- IP-block on the native API → `_ip_blocked` flag set on the adapter; subsequent videos in the same run skip the native API and go straight to Whisper.
- The `YouTubeAdapter` instance is now shared across creators within a run (see `ingest_all`) so this flag persists across the whole run, not just one creator.
- `YouTubeIpBlocked` is only raised when **both** the native API and the Whisper fallback fail. With Whisper configured, IP-blocks are silently absorbed.

This unblocks the rest of Phase 1b backfill at the cost of Whisper compute (~30–90s per 10-min video on CPU with `base.en`).

## What we're NOT doing

- **No silent fallback to "empty transcript".** A blocked IP looks identical to a missing transcript without explicit detection — that's exactly how the original backfill produced misleading "0 segments" rows for 5 creators.
- **No infinite retries on a single video.** Three attempts via `tenacity`, then either the transcript is genuinely missing (continue) or the IP is blocked (abort the whole run).
- **No proxy without a clear product reason.** Adding paid infra to a research project is premature; Whisper is free and works.
