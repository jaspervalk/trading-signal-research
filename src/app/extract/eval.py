"""Evaluation harness for the call extractor.

Loads `data/gold/extraction_gold.jsonl`, runs the extractor on each item,
and computes:
  - call-detection confusion matrix (TP / FP / FN / TN at the "is this a call?" level)
  - per-field accuracy on positives:
      ticker (exact match)
      direction (exact match)
      entry_type (exact match)
      entry/target/stop prices (within ±1% tolerance)
      timeframe (exact match)

The harness accepts any object with an `extract(text) -> ExtractionResult`
contract, so unit tests + the eval notebook can both inject mocks.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import REPO_ROOT
from app.extract.confidence import compute_final_confidence
from app.extract.llm_extractor import ExtractionResult
from app.extract.schemas import LLMExtractedCall
from app.extract.validator import validate
from app.normalize.tickers import Universe, load_universe


GOLD_PATH = REPO_ROOT / "data" / "gold" / "extraction_gold.jsonl"


@dataclass
class GoldItem:
    id: str
    source_text: str
    expected: dict[str, Any] | None  # None = no-call expected
    notes: str = ""


class _ExtractorLike(Protocol):
    def extract_with_validation(self, text: str) -> ExtractionResult: ...


def load_gold(path: Path | None = None) -> list[GoldItem]:
    p = path or GOLD_PATH
    items: list[GoldItem] = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            row = json.loads(line)
            items.append(
                GoldItem(
                    id=row["id"],
                    source_text=row["source_text"],
                    expected=row.get("expected"),
                    notes=row.get("notes", ""),
                )
            )
    return items


@dataclass
class FieldHits:
    correct: int = 0
    total: int = 0

    def add(self, hit: bool) -> None:
        self.total += 1
        if hit:
            self.correct += 1

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalReport:
    n_items: int = 0
    tp: int = 0   # gold positive, predicted positive
    fp: int = 0   # gold negative, predicted positive
    fn: int = 0   # gold positive, predicted negative
    tn: int = 0   # gold negative, predicted negative

    field_hits: dict[str, FieldHits] = field(default_factory=dict)
    per_item: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def summary_table(self) -> list[tuple[str, str]]:
        rows = [
            ("n_items", str(self.n_items)),
            ("call_detection.tp", str(self.tp)),
            ("call_detection.fp", str(self.fp)),
            ("call_detection.fn", str(self.fn)),
            ("call_detection.tn", str(self.tn)),
            ("call_detection.precision", f"{self.precision:.3f}"),
            ("call_detection.recall", f"{self.recall:.3f}"),
            ("call_detection.f1", f"{self.f1:.3f}"),
        ]
        for fname, h in sorted(self.field_hits.items()):
            rows.append(
                (f"field.{fname}.accuracy", f"{h.accuracy:.3f}  ({h.correct}/{h.total})")
            )
        return rows


def _price_within_tol(predicted: float | None, expected: float | None, tol_pct: float = 0.01) -> bool:
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    if expected == 0:
        return abs(predicted) < tol_pct
    return abs(predicted - expected) / abs(expected) <= tol_pct


def evaluate(
    extractor: _ExtractorLike,
    *,
    gold: Iterable[GoldItem] | None = None,
    universe: Universe | None = None,
    use_two_pass: bool = True,
) -> EvalReport:
    """Run the extractor over each gold item; return an EvalReport."""
    gold_list = list(gold) if gold is not None else load_gold()
    universe = universe or load_universe()

    report = EvalReport(n_items=len(gold_list))

    for item in gold_list:
        if use_two_pass:
            res = extractor.extract_with_validation(item.source_text)
        else:
            # Single-pass via duck typing.
            res = getattr(extractor, "extract", extractor.extract_with_validation)(item.source_text)

        predicted_call: LLMExtractedCall | None = None
        if res.call is not None:
            v = validate(res.call, source_text=item.source_text, universe=universe)
            if v.accepted_call is not None:
                predicted_call = v.accepted_call
                _ = compute_final_confidence(predicted_call, v)

        gold_is_call = item.expected is not None
        pred_is_call = predicted_call is not None

        if gold_is_call and pred_is_call:
            report.tp += 1
        elif gold_is_call and not pred_is_call:
            report.fn += 1
        elif not gold_is_call and pred_is_call:
            report.fp += 1
        else:
            report.tn += 1

        # Per-field hit counts only on items where both gold and prediction are calls.
        if gold_is_call and pred_is_call:
            exp = item.expected or {}
            assert predicted_call is not None
            for fname, ok in [
                ("ticker", predicted_call.ticker == exp.get("ticker")),
                ("direction", predicted_call.direction == exp.get("direction")),
                ("entry_type", predicted_call.entry_type == exp.get("entry_type")),
                ("entry_price", _price_within_tol(predicted_call.entry_price, exp.get("entry_price"))),
                ("target_price", _price_within_tol(predicted_call.target_price, exp.get("target_price"))),
                ("stop_price", _price_within_tol(predicted_call.stop_price, exp.get("stop_price"))),
                ("timeframe", predicted_call.timeframe == exp.get("timeframe", "unspecified")),
            ]:
                report.field_hits.setdefault(fname, FieldHits()).add(ok)

        report.per_item.append(
            {
                "id": item.id,
                "gold_is_call": gold_is_call,
                "pred_is_call": pred_is_call,
                "predicted": predicted_call.model_dump() if predicted_call else None,
                "expected": item.expected,
                "notes": item.notes,
            }
        )

    return report
