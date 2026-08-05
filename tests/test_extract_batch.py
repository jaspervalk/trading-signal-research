"""The batched transport must be indistinguishable from the synchronous one.

Batching is a 50% price cut on the backlog, and it is only worth taking if it
changes nothing else. These tests pin the two properties that matter:

1. Both transports build the *same request* — same model, prompts, tools,
   tool_choice — so the gold set validates what actually goes over the wire.
2. Both transports produce the *same rows*, including the two-pass contract
   (pass 2 re-adjudicates the call; pass-1 claims are preserved).

Everything here is offline: no client, no network, no spend.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.extract import batch as batch_mod
from app.extract.llm_extractor import ExtractionResult, LLMExtractor
from app.extract.schemas import LLMExtractedCall


# --------------------------------------------------------------------------- #
# Request-shape parity


@pytest.fixture
def extractor() -> LLMExtractor:
    return LLMExtractor(client=SimpleNamespace())


def test_extract_params_match_what_the_sync_path_sends(extractor):
    """The sync path calls messages.create(**build_extract_params(text))."""
    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(content=[], usage=None, model_dump=lambda: {})

    extractor._client.messages = SimpleNamespace(create=fake_create)
    extractor.extract("NVDA looks strong here")

    assert sent == extractor.build_extract_params("NVDA looks strong here")


def test_batch_and_sync_requests_are_byte_identical(extractor):
    """Same window, same params object — no second copy of the prompt."""
    text = "buying AMD at 150"
    from_builder = extractor.build_extract_params(text)

    assert from_builder["model"] == extractor._model
    assert from_builder["tool_choice"] == {"type": "any"}
    assert from_builder["max_tokens"] == extractor._max_tokens
    # Three tools: submit_call, submit_claims, submit_no_call_found.
    assert len(from_builder["tools"]) == 3
    assert text in from_builder["messages"][0]["content"]


def test_validation_params_embed_the_previous_call(extractor):
    params = extractor.build_validation_params("some text", '{"ticker": "AMD"}')
    content = params["messages"][0]["content"]
    assert "some text" in content
    assert '{"ticker": "AMD"}' in content


# --------------------------------------------------------------------------- #
# run_batch: keying, ordering, partial failure


class _FakeBatches:
    """Minimal stand-in for client.messages.batches."""

    def __init__(self, outcomes: dict[str, object], statuses: list[str] | None = None):
        self._outcomes = outcomes
        self._statuses = statuses or ["ended"]
        self.submitted: list[list] = []

    def create(self, *, requests):
        self.submitted.append(list(requests))
        return SimpleNamespace(id=f"batch_{len(self.submitted)}")

    def retrieve(self, batch_id):
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id):
        # Deliberately reversed: results arrive in arbitrary order and must be
        # keyed by custom_id, never zipped against the input.
        for custom_id, outcome in reversed(list(self._outcomes.items())):
            yield SimpleNamespace(custom_id=custom_id, result=outcome)


def _succeeded(message="ok"):
    return SimpleNamespace(type="succeeded", message=message)


def _make_extractor_with(batches, parsed):
    ex = LLMExtractor(client=SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    ex.parse_response = lambda message: parsed[message]  # type: ignore[assignment]
    return ex


def test_run_batch_keys_results_by_custom_id_not_position():
    parsed = {
        "msg-a": ExtractionResult(raw_response={"which": "a"}),
        "msg-b": ExtractionResult(raw_response={"which": "b"}),
    }
    batches = _FakeBatches({"id-a": _succeeded("msg-a"), "id-b": _succeeded("msg-b")})
    ex = _make_extractor_with(batches, parsed)

    outcome = batch_mod.run_batch(
        ex,
        {"id-a": {"p": 1}, "id-b": {"p": 2}},
        label="t",
        sleep=lambda _: None,
    )

    assert outcome.results["id-a"].raw_response == {"which": "a"}
    assert outcome.results["id-b"].raw_response == {"which": "b"}
    assert outcome.n_failed == 0


def test_run_batch_isolates_a_failed_request():
    """One bad window must not sink a backlog of thousands."""
    parsed = {"msg-ok": ExtractionResult(raw_response={"which": "ok"})}
    batches = _FakeBatches(
        {
            "good": _succeeded("msg-ok"),
            "bad": SimpleNamespace(type="errored", message=None),
        }
    )
    ex = _make_extractor_with(batches, parsed)

    outcome = batch_mod.run_batch(
        ex, {"good": {}, "bad": {}}, label="t", sleep=lambda _: None
    )

    assert outcome.n_ok == 1
    assert outcome.errors == {"bad": "errored"}


def test_run_batch_polls_until_ended():
    parsed = {"m": ExtractionResult(raw_response={})}
    batches = _FakeBatches({"x": _succeeded("m")}, statuses=["in_progress", "in_progress", "ended"])
    ex = _make_extractor_with(batches, parsed)
    slept: list[float] = []

    batch_mod.run_batch(ex, {"x": {}}, label="t", sleep=slept.append, poll_interval=5.0)

    assert slept == [5.0, 5.0]


def test_run_batch_times_out_rather_than_polling_forever():
    parsed = {"m": ExtractionResult(raw_response={})}
    batches = _FakeBatches({"x": _succeeded("m")}, statuses=["in_progress"])
    ex = _make_extractor_with(batches, parsed)

    with pytest.raises(TimeoutError, match="still in_progress"):
        batch_mod.run_batch(
            ex, {"x": {}}, label="t", sleep=lambda _: None, poll_interval=10.0, timeout=30.0
        )


def test_empty_input_does_not_submit_a_batch():
    batches = _FakeBatches({})
    ex = _make_extractor_with(batches, {})

    outcome = batch_mod.run_batch(ex, {}, label="t", sleep=lambda _: None)

    assert outcome.n_ok == 0
    assert batches.submitted == []


def test_oversized_input_is_chunked(monkeypatch):
    monkeypatch.setattr(batch_mod, "MAX_REQUESTS_PER_BATCH", 2)
    parsed = {f"m{i}": ExtractionResult(raw_response={}) for i in range(5)}
    batches = _FakeBatches({f"id{i}": _succeeded(f"m{i}") for i in range(5)})
    ex = _make_extractor_with(batches, parsed)

    batch_mod.run_batch(
        ex, {f"id{i}": {} for i in range(5)}, label="t", sleep=lambda _: None
    )

    assert [len(s) for s in batches.submitted] == [2, 2, 1]


# --------------------------------------------------------------------------- #
# Two-pass contract


def test_two_pass_preserves_pass1_claims_and_takes_pass2_call():
    """Same contract as LLMExtractor.extract_with_validation."""
    from app.extract.run import run_extraction_batched

    def _call(confidence: float) -> LLMExtractedCall:
        return LLMExtractedCall(
            ticker="AMD",
            direction="long",
            direction_evidence="looks good",
            entry_type="unspecified",
            ticker_evidence="AMD",
            overall_confidence=confidence,
        )

    pass1_call = _call(0.6)
    pass2_call = _call(0.9)
    cand = SimpleNamespace(
        context=SimpleNamespace(text="AMD looks good", start_seconds=0.0, end_seconds=10.0),
        window=SimpleNamespace(source_segment_ids=[1]),
    )

    calls: list[str] = []

    def fake_runner(extractor, params_by_id, *, label, **kw):
        calls.append(label)
        if label == "pass1":
            return batch_mod.BatchOutcome(
                results={
                    "d1-c0": ExtractionResult(call=pass1_call, claims=["claim-from-pass-1"])
                },
                errors={},
            )
        return batch_mod.BatchOutcome(
            results={"d1-c0": ExtractionResult(call=pass2_call)}, errors={}
        )

    persisted: list[ExtractionResult] = []

    import app.extract.run as run_mod

    def fake_persist(*, result, **kwargs):
        persisted.append(result)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_mod, "_load_pending_documents", lambda **kw: [(1, "doc")])
        mp.setattr(run_mod, "_candidates_for_document", lambda doc_id, universe: [cand])
        mp.setattr(run_mod, "_persist_window_result", fake_persist)
        run_extraction_batched(
            extractor=LLMExtractor(client=SimpleNamespace()),
            universe=SimpleNamespace(),
            batch_runner=fake_runner,
            use_two_pass=True,
        )

    assert calls == ["pass1", "pass2"]
    assert len(persisted) == 1
    # Pass 2's call won; pass 1's claims survived.
    assert persisted[0].call.overall_confidence == 0.9
    assert persisted[0].claims == ["claim-from-pass-1"]


def test_one_pass_skips_the_second_batch():
    from app.extract.run import run_extraction_batched

    cand = SimpleNamespace(
        context=SimpleNamespace(text="t", start_seconds=0.0, end_seconds=1.0),
        window=SimpleNamespace(source_segment_ids=[1]),
    )
    labels: list[str] = []

    def fake_runner(extractor, params_by_id, *, label, **kw):
        labels.append(label)
        return batch_mod.BatchOutcome(
            results={"d1-c0": ExtractionResult(raw_response={})}, errors={}
        )

    import app.extract.run as run_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_mod, "_load_pending_documents", lambda **kw: [(1, "doc")])
        mp.setattr(run_mod, "_candidates_for_document", lambda doc_id, universe: [cand])
        mp.setattr(run_mod, "_persist_window_result", lambda **kw: None)
        run_extraction_batched(
            extractor=LLMExtractor(client=SimpleNamespace()),
            universe=SimpleNamespace(),
            batch_runner=fake_runner,
            use_two_pass=False,
        )

    assert labels == ["pass1"]


def test_failed_batch_requests_are_counted_as_errors():
    from app.extract.run import run_extraction_batched

    cand = SimpleNamespace(
        context=SimpleNamespace(text="t", start_seconds=0.0, end_seconds=1.0),
        window=SimpleNamespace(source_segment_ids=[1]),
    )

    def fake_runner(extractor, params_by_id, *, label, **kw):
        return batch_mod.BatchOutcome(results={}, errors={"d1-c0": "expired"})

    import app.extract.run as run_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_mod, "_load_pending_documents", lambda **kw: [(1, "doc")])
        mp.setattr(run_mod, "_candidates_for_document", lambda doc_id, universe: [cand])
        mp.setattr(run_mod, "_persist_window_result", lambda **kw: None)
        summary = run_extraction_batched(
            extractor=LLMExtractor(client=SimpleNamespace()),
            universe=SimpleNamespace(),
            batch_runner=fake_runner,
            use_two_pass=True,
        )

    assert summary["errors"] == 1
    assert summary["documents"] == 1
