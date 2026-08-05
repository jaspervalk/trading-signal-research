"""Batches-API transport for the extractor.

Same model, same prompts, same tools, same validator — 50% of the price. The
extraction backlog is the textbook batch workload: a few thousand independent
windows, no latency requirement, run once.

What this module does NOT do is re-implement extraction. It builds requests
from `LLMExtractor.build_*_params` and parses them back through
`LLMExtractor.parse_response`, so the request shape the gold set validates is
the one that goes over the wire. This file owns exactly one thing: getting a
pile of requests to the Batches API and the results back, keyed.

Two-pass extraction needs two rounds — pass 2's prompt embeds pass 1's call —
so a two-pass run submits a second batch once the first has landed.

Results arrive in arbitrary order and are keyed by `custom_id`; never zip them
against the input list.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from app.extract.llm_extractor import ExtractionResult, LLMExtractor
from app.logging import get_logger

log = get_logger(__name__)

# The API caps a batch at 100k requests / 256MB. We chunk well under both so a
# single oversized backlog can't fail the whole submission.
MAX_REQUESTS_PER_BATCH = 5_000

POLL_INTERVAL_SECONDS = 20.0
# Most batches finish inside an hour; the API's own ceiling is 24h.
POLL_TIMEOUT_SECONDS = 24 * 60 * 60


@dataclass
class BatchOutcome:
    """Per-request result, keyed by the caller's `custom_id`."""

    results: dict[str, ExtractionResult]
    errors: dict[str, str]

    @property
    def n_ok(self) -> int:
        return len(self.results)

    @property
    def n_failed(self) -> int:
        return len(self.errors)


def run_batch(
    extractor: LLMExtractor,
    params_by_id: dict[str, dict[str, Any]],
    *,
    label: str,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    timeout: float = POLL_TIMEOUT_SECONDS,
) -> BatchOutcome:
    """Submit `params_by_id`, wait for completion, return parsed results.

    `params_by_id` maps a caller-chosen `custom_id` to the kwargs that would
    otherwise be passed to `messages.create`. Requests that error, are
    cancelled, or expire land in `BatchOutcome.errors` rather than raising —
    one bad window should not sink a backlog of thousands.
    """
    if not params_by_id:
        return BatchOutcome(results={}, errors={})

    client = extractor.client
    results: dict[str, ExtractionResult] = {}
    errors: dict[str, str] = {}

    for chunk in _chunked(sorted(params_by_id.items()), MAX_REQUESTS_PER_BATCH):
        requests = [
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(**params),
            )
            for custom_id, params in chunk
        ]
        batch = client.messages.batches.create(requests=requests)
        log.info("extract.batch.submitted", label=label, batch_id=batch.id, n=len(requests))

        _await_completion(
            client,
            batch.id,
            label=label,
            sleep=sleep,
            poll_interval=poll_interval,
            timeout=timeout,
        )

        for entry in client.messages.batches.results(batch.id):
            custom_id = entry.custom_id
            kind = entry.result.type
            if kind == "succeeded":
                results[custom_id] = extractor.parse_response(entry.result.message)
            else:
                # errored / canceled / expired all mean "no usable output".
                errors[custom_id] = kind
                log.warning(
                    "extract.batch.request_failed",
                    label=label,
                    batch_id=batch.id,
                    custom_id=custom_id,
                    outcome=kind,
                )

        log.info(
            "extract.batch.collected",
            label=label,
            batch_id=batch.id,
            ok=len(results),
            failed=len(errors),
        )

    return BatchOutcome(results=results, errors=errors)


def _await_completion(
    client: Any,
    batch_id: str,
    *,
    label: str,
    sleep: Callable[[float], None],
    poll_interval: float,
    timeout: float,
) -> None:
    waited = 0.0
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return
        if waited >= timeout:
            raise TimeoutError(
                f"batch {batch_id} ({label}) still {batch.processing_status} "
                f"after {waited:.0f}s"
            )
        log.info(
            "extract.batch.waiting",
            label=label,
            batch_id=batch_id,
            status=batch.processing_status,
            waited_seconds=int(waited),
        )
        sleep(poll_interval)
        waited += poll_interval


def _chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


__all__ = ["BatchOutcome", "MAX_REQUESTS_PER_BATCH", "run_batch"]
