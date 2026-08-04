# Discord source adapter — design memo

**Status:** scoping, not started
**Date drafted:** 2026-05-28
**Author note:** Design only — no code in this slice. Grounds Phase 7 work without committing to it.

## Goal + scope

Add a `DiscordSourceAdapter` so analyst Discord servers (paid newsletter rooms, stockpicking communities, AI-fund chat) flow through the same `Source → Document → Segment → [Extraction] → TradeCall/Claim → TickerSignal` pipeline as YouTube. **Zero downstream code changes** — extraction, validator, backtest, ranking all consume `RawDocument` / `RawSegment` agnostically per [ADR 0001](decisions/0001-source-abstraction.md).

Out of scope here: building it, training extractor on Discord-style messages, monetization/legal posture for paid rooms.

## Where it plugs in

The contract from [src/app/sources/base.py](../src/app/sources/base.py) is already source-agnostic:

```python
class SourceAdapter(ABC):
    source_type: str  # "discord"
    def list_recent_documents(channel_external_id, *, since=None, limit=None) -> Iterable[str]: ...
    def fetch_document(external_id) -> RawDocument: ...
```

`RawDocument` only needs: `source_type`, `external_id`, `channel_external_id`, `title?`, `description?`, `posted_at` (UTC tz-aware), `url?`, `duration_seconds?`, `segments: list[RawSegment]`.

The fields are already loose enough to fit Discord — `duration_seconds` becomes `None`, `title` becomes the thread title (or first message stub), `description` becomes the channel topic snapshot, `url` is `https://discord.com/channels/<guild_id>/<channel_id>/<message_id>`.

Adding the adapter = (a) implement the two abstract methods, (b) add a branch in `ingest/run.py` `get_adapter()`, (c) extend `configs/creators.yaml` schema to carry guild/channel pairs.

## Key design decisions (the actual content)

### 1. Auth: bot token, not user token

Three options exist; only one is viable:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **User token (self-bot)** | Reads any server the user is in, including paid rooms | Explicitly **violates Discord ToS**; account ban risk; Discord actively detects | **Reject.** |
| **Bot token via official API** | First-party, persistent, rate-limited but well-documented | Requires server admin to invite the bot; many analyst rooms won't allow this | **Default.** |
| **Webhook scraping** (analyst posts also push to a webhook the user owns) | Works for newsletter-style rooms that already syndicate | Niche; only some rooms support it | Fallback. |

**Decision:** Bot token + official `discord.py` SDK. If a target room won't invite a bot, that room is out of scope — we do not self-bot. This is the same posture as ADR 0004 picked for YouTube (no scraping endpoints that block).

**Stored where:** `DISCORD_BOT_TOKEN` in `.env`, loaded via `app.config.load_env()` next to `ANTHROPIC_API_KEY` / `YOUTUBE_API_KEY`. Optional per-creator override via `configs/creators.yaml` (different bots for different servers if needed).

### 2. Document granularity: thread first, with daily-batch fallback

The user-facing question is "what is the unit of decision?" — same framing as ADR 0005 ("the ticker, not the creator"). For Discord:

| Granularity | Document = | Pros | Cons |
|---|---|---|---|
| **Per message** | one Discord message | Cleanest mapping; each msg is one segment, document==segment | Tons of single-line garbage docs; extraction cost explodes; per-call confidence collapses |
| **Per thread** | one Discord thread (started via "Create Thread" or auto-thread on certain channels) | Matches analyst behavior: "$NVDA setup" gets its own thread | Many trade rooms don't use threads — flat channels are common |
| **Daily message-batch per author** | all messages from one user on one calendar day, grouped chronologically | Mirrors a "morning call" YouTube doc; aligns with how analysts post in flat channels | Coarse — a single batch may contain 3 unrelated tickers |
| **Burst-batch** | contiguous run of messages from one author with < N min between, capped at M messages | Tracks the natural rhythm of a real analyst | Tunable params (N, M) per server; more state |

**Decision:** Burst-batch with `gap_minutes=15, max_messages=40` as defaults, **per-author**, configurable per channel in `creators.yaml`. Thread-aware: if a message lives in a thread, the thread is the boundary regardless of timing.

Why: ADR 0001 already says "we will pick after surveying real traffic." Burst-batching is the version that works for both threaded and flat channels and matches how creators talk. Per-author is critical — a busy room interleaves 5 voices and we score per creator.

**`external_id` shape:** `discord:<guild_id>:<channel_id>:<first_message_id>` (string). Stable; first message ID anchors the batch even if later messages are edited/deleted.

**`segments` shape:** one `RawSegment` per message. `start_seconds` = seconds since first message in batch (0, Δ1, Δ2 …). `end_seconds` = `start_seconds` (instantaneous). `speaker_label` = author display name (always one author per batch, so this is constant — but the field is useful when we later merge multi-author batches if we change our minds).

### 3. Rate limits

Discord global limits: 50 req/s per bot token, 10k events/s for gateway. Per-route limits returned dynamically via response headers (`X-RateLimit-Remaining`, `X-RateLimit-Reset-After`). Backoff is mandatory — Discord will 429 aggressively.

For ingest:
- Use the **gateway WebSocket** (persistent) for live message events on tracked channels. This is push, not pull — solves the polling problem YouTube has.
- Use the **REST API** only for backfill (`channels/{id}/messages?before=…`) and one-off re-fetch.
- All HTTP calls go through `discord.py`'s built-in rate-limiter; no custom limiter needed.

**Implication:** Discord ingest is fundamentally different from YouTube ingest. The daily-cron model (`scripts/daily_run.sh`) still works for backfill, but the natural shape is a **long-running gateway consumer** that buffers messages and emits closed batches when the burst-gap fires.

**Operational consequence:** add a `tsr discord-watch` long-running command (separate from `tsr ingest`) plus a launchd job that keeps it up. Idempotent persistence still applies — the watcher writes the same `Document` row a backfill `tsr ingest` would.

### 4. Schema changes

Minimal, but real:

- **`Document.duration_seconds`** — already nullable, no change.
- **`Document.title`** — currently used for video title; for Discord, becomes a synthesized batch title like `"@analyst — 2026-05-28T14:32Z (5 msgs, 1 ticker)"`. Pure metadata; no schema change.
- **`SourceChannel`** — no change. `external_id` becomes `"<guild_id>:<channel_id>"`; `source_type` is `"discord"`.
- **`TranscriptSegment`** — needs a new optional column `author_external_id` to track Discord user IDs **inside** a batch if we later relax the per-author rule. **Defer this** — for v1, batch is per-author, so `Document.creator_id` is sufficient. Worth flagging in the migration when we add it.

One **non-obvious** schema concern: `creators.yaml` currently has a 1:1 `creator → channel` shape. For Discord, one creator typically posts to multiple channels in a guild (general, alerts, premium). We need:

```yaml
- creator_id: leopold_analyst
  display_name: "@leopold"
  channels:
    - source_type: discord
      external_id: "111111111:222222222"   # guild_id:channel_id
      role: "alerts"
    - source_type: discord
      external_id: "111111111:333333333"
      role: "premium-watchlist"
    - source_type: youtube
      external_id: "UCxxxx"
      role: "main"
  active: true
```

This is a real schema change to the YAML and the loader in `src/app/ingest/`. Worth scoping as part of slice 1 of Phase 7 — *before* the adapter — because it also future-proofs multi-channel YouTube creators.

### 5. Extraction implications

The extractor (ADR 0002) was tuned on YouTube transcripts. Discord messages differ:
- **Much shorter** — a "call" can be one line: `"$NVDA long here 198, stop 194, target 235"`. The prefilter regex still catches it (ticker mention + dollar value). The LLM stage works fine. The **evidence-quote requirement** (ADR 0002) is trivial — the entire message often is the quote.
- **More slang / abbreviations** — `"NVDA looks juicy, full port, 1/0"`. The validator's `entry_price ±50%` rule still applies; the rest still works.
- **Higher noise floor** — chat rooms have a lot of "lol", reactions, off-topic. Prefilter must aggressively reject; we expect Discord's prefilter pass rate to be **lower** than YouTube's 5-10%, maybe 1-2%.

**No code changes needed** to extract — just gold-set extension before we trust the numbers. Plan to label ~50 Discord docs the same way we labeled YouTube before Phase 4.

### 6. What this memo deliberately does NOT decide

- Which servers / creators to ingest first (legal + auth pending; user decision).
- Whether the watcher gets a Phase 7 ORM table (`DiscordSession` etc.) — likely yes for resume-from-gap tracking, but design once the prototype runs.
- Re-edit / delete handling. Discord supports both. Default: snapshot at first see, ignore edits, soft-flag deletions. Revisit if it bites us.
- Premium-content storage. If we ingest a paid room, we don't ship its contents anywhere; the data stays in the local SQLite. **Add a per-channel `egress: private` flag in `creators.yaml` to prevent that channel from appearing in any exportable view.**

## Phased rollout (when greenlit)

1. **Slice 1 — multi-channel YAML.** Refactor `creators.yaml` to support multiple channels per creator; update loader; verify YouTube still works. **No Discord code.** Smallest reversible step.
2. **Slice 2 — adapter scaffold.** `discord.py` dep, `DiscordSourceAdapter` reading from a fixture transcript dump, `tsr ingest --source discord` working for one historical day, persisted into the existing `Document` / `TranscriptSegment` tables.
3. **Slice 3 — live gateway.** Long-running `tsr discord-watch` with burst-batch close logic and idempotent persistence. Launchd job.
4. **Slice 4 — extractor calibration.** Run existing extractor over 50 hand-picked Discord docs, extend gold set, accept the regression we see, iterate prompts only if precision drops > 5pp.
5. **Slice 5 — TickerSignal aggregation.** Verify per-ticker × window × signal-type aggregation correctly merges Discord + YouTube signals (it should already; ADR 0006 is source-agnostic).

Total estimate: ~1.5-2 weeks of focused work for slices 1-3, plus calibration work in slice 4 that's open-ended.

## Cross-references

- [ADR 0001 — source abstraction](decisions/0001-source-abstraction.md) — the contract this implements
- [ADR 0002 — hybrid extraction](decisions/0002-extraction-hybrid.md) — what the extractor expects
- [ADR 0004 — transcript ingestion + IP-block](decisions/0004-transcript-ingestion.md) — precedent for "no scraping / use first-party APIs"
- [ADR 0005 — product pivot](decisions/0005-product-pivot-decision-support.md) — load-bearing; Discord doesn't change the ticker-first framing
- [ADR 0006 — claims + TickerSignals](decisions/0006-claims-and-ticker-signals.md) — the aggregation step Discord plugs into unchanged
