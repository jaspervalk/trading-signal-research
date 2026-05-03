"""Tests for the extractor eval harness using a mock LLM extractor."""

from __future__ import annotations

from app.extract.eval import GoldItem, evaluate, load_gold
from app.extract.llm_extractor import ExtractionResult
from app.extract.schemas import LLMExtractedCall, NoCallFound
from app.normalize.tickers import Universe


class _FakeExtractor:
    """Lookup-table extractor: returns canned results keyed by gold item text."""

    def __init__(self, by_text: dict[str, ExtractionResult]):
        self._by_text = by_text

    def extract(self, text: str) -> ExtractionResult:
        return self._by_text.get(text, ExtractionResult(call=None, no_call=None, raw_response={}))

    def extract_with_validation(self, text: str) -> ExtractionResult:
        return self.extract(text)


def _u(*tickers: str) -> Universe:
    u = Universe()
    for t in tickers:
        u.by_ticker[t] = {"name": t.title(), "sector": "X"}
    return u


def test_gold_file_loads():
    items = load_gold()
    assert len(items) >= 15
    assert any(it.expected is not None for it in items), "must have positive examples"
    assert any(it.expected is None for it in items), "must have negative examples"


def test_eval_perfect_predictions():
    src_pos = "NVDA looks strong above 920"
    src_neg = "general market commentary"

    pred = LLMExtractedCall(
        is_trade_call=True,
        ticker="NVDA",
        ticker_evidence="NVDA looks strong",
        direction="long",
        direction_evidence="looks strong",
        entry_type="trigger_above",
        entry_price=920.0,
        entry_evidence="above 920",
        overall_confidence=0.9,
    )

    extractor = _FakeExtractor(
        {
            src_pos: ExtractionResult(call=pred, no_call=None, raw_response={}),
            src_neg: ExtractionResult(call=None, no_call=NoCallFound(reason="no call"), raw_response={}),
        }
    )

    gold = [
        GoldItem(
            id="t1",
            source_text=src_pos,
            expected={
                "is_trade_call": True,
                "ticker": "NVDA",
                "direction": "long",
                "entry_type": "trigger_above",
                "entry_price": 920.0,
                "target_price": None,
                "stop_price": None,
                "timeframe": "unspecified",
            },
        ),
        GoldItem(id="t2", source_text=src_neg, expected=None),
    ]

    report = evaluate(extractor, gold=gold, universe=_u("NVDA"))
    assert report.tp == 1
    assert report.tn == 1
    assert report.fp == 0
    assert report.fn == 0
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.field_hits["ticker"].accuracy == 1.0
    assert report.field_hits["direction"].accuracy == 1.0


def test_eval_counts_false_positive():
    src = "general macro talk"
    pred = LLMExtractedCall(
        is_trade_call=True,
        ticker="NVDA",
        ticker_evidence="general macro talk",
        direction="long",
        direction_evidence="general macro talk",
        entry_type="market",
        overall_confidence=0.5,
    )
    extractor = _FakeExtractor({src: ExtractionResult(call=pred, no_call=None, raw_response={})})
    gold = [GoldItem(id="t", source_text=src, expected=None)]
    report = evaluate(extractor, gold=gold, universe=_u("NVDA"))
    assert report.fp == 1
    assert report.tn == 0


def test_eval_counts_false_negative():
    src = "NVDA looks strong above 920"
    extractor = _FakeExtractor({src: ExtractionResult(call=None, no_call=None, raw_response={})})
    gold = [
        GoldItem(
            id="t",
            source_text=src,
            expected={
                "is_trade_call": True,
                "ticker": "NVDA",
                "direction": "long",
                "entry_type": "trigger_above",
                "entry_price": 920.0,
                "target_price": None,
                "stop_price": None,
                "timeframe": "unspecified",
            },
        )
    ]
    report = evaluate(extractor, gold=gold, universe=_u("NVDA"))
    assert report.fn == 1
    assert report.tp == 0
