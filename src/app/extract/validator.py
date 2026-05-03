"""Rule-based validator for LLM-extracted calls.

Runs after the pydantic schema check. Catches issues the schema can't:
  - hallucinated tickers (not in our universe)
  - evidence quotes that don't actually appear in the source
  - implausible prices vs. the universe / context

Returns a `ValidationResult` carrying:
  - `accepted_call`: a possibly-modified copy of the LLM call with bad
    fields nulled out, OR None if the call should be rejected wholesale.
  - `failures`: list of human-readable reasons.
  - `rule_confidence`: derived from how many checks passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.extract.schemas import LLMExtractedCall
from app.normalize.tickers import Universe


@dataclass
class ValidationResult:
    accepted_call: LLMExtractedCall | None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rule_confidence: float = 0.0
    nulled_fields: list[str] = field(default_factory=list)


_NORMALIZE_WS = re.compile(r"\s+")


def _normalize_for_match(s: str) -> str:
    return _NORMALIZE_WS.sub(" ", s).strip().lower()


def _quote_appears_in_source(quote: str | None, source: str) -> bool:
    if not quote:
        return False
    return _normalize_for_match(quote) in _normalize_for_match(source)


def validate(
    call: LLMExtractedCall,
    *,
    source_text: str,
    universe: Universe,
    market_price_at_post: float | None = None,
    price_tolerance_pct: float = 0.5,
) -> ValidationResult:
    """Apply rule checks to an LLM-extracted call.

    `market_price_at_post` is the actual market price for `call.ticker` at the
    `posted_at` of the source document, if known. If provided, prices outside
    [(1 - tol) * p, (1 + tol) * p] are nulled out.
    """
    failures: list[str] = []
    warnings: list[str] = []
    nulled: list[str] = []

    # 1. Ticker must exist in universe.
    if not universe.has(call.ticker):
        failures.append(f"ticker_not_in_universe: {call.ticker!r}")
        return ValidationResult(
            accepted_call=None,
            failures=failures,
            warnings=warnings,
            rule_confidence=0.0,
            nulled_fields=nulled,
        )

    # 2. Required evidence quotes must appear in source.
    if not _quote_appears_in_source(call.ticker_evidence, source_text):
        failures.append("ticker_evidence_not_in_source")
        return ValidationResult(
            accepted_call=None,
            failures=failures,
            warnings=warnings,
            rule_confidence=0.0,
            nulled_fields=nulled,
        )

    if not _quote_appears_in_source(call.direction_evidence, source_text):
        failures.append("direction_evidence_not_in_source")
        # Soft-handle: drop direction confidence rather than rejecting the call.
        # We don't have a way to mutate the pydantic model partially without
        # rebuilding it; keep the field but warn.
        warnings.append("direction_evidence_unsupported")

    # 3. Optional-field evidence checks: null those that fail.
    updates: dict[str, object] = {}
    for value_field, evidence_field in [
        ("entry_price", "entry_evidence"),
        ("target_price", "target_evidence"),
        ("stop_price", "stop_evidence"),
    ]:
        v = getattr(call, value_field)
        e = getattr(call, evidence_field)
        if v is None:
            continue
        if not _quote_appears_in_source(e, source_text):
            updates[value_field] = None
            updates[evidence_field] = None
            nulled.append(value_field)

    # 4. Price plausibility against market_price_at_post.
    if market_price_at_post is not None and market_price_at_post > 0:
        lo = market_price_at_post * (1.0 - price_tolerance_pct)
        hi = market_price_at_post * (1.0 + price_tolerance_pct)
        for value_field, evidence_field in [
            ("entry_price", "entry_evidence"),
            ("target_price", "target_evidence"),
            ("stop_price", "stop_evidence"),
        ]:
            v = updates.get(value_field, getattr(call, value_field))
            if v is None:
                continue
            if not (lo <= v <= hi):
                updates[value_field] = None
                updates[evidence_field] = None
                if value_field not in nulled:
                    nulled.append(value_field)
                warnings.append(
                    f"{value_field}={v} outside ±{int(price_tolerance_pct*100)}% of "
                    f"market_price={market_price_at_post:.2f}; nulled."
                )

    # 5. If the entry_type required entry_price and it got nulled, downgrade entry_type.
    new_entry_type = updates.get("entry_type", call.entry_type)
    new_entry_price = updates.get("entry_price", call.entry_price)
    if new_entry_type in {"limit", "trigger_above", "trigger_below"} and new_entry_price is None:
        updates["entry_type"] = "unspecified"
        warnings.append(
            f"entry_type was {new_entry_type!r} but entry_price was nulled; "
            "downgrading to 'unspecified'."
        )

    # Build the accepted call (copy with updates applied).
    accepted = call.model_copy(update=updates) if updates else call

    # Confidence: 1.0 baseline, minus 0.15 per warning, minus 0.25 per nulled field.
    rule_conf = 1.0 - 0.15 * len(warnings) - 0.25 * len(nulled)
    rule_conf = max(0.0, min(1.0, rule_conf))

    return ValidationResult(
        accepted_call=accepted,
        failures=failures,
        warnings=warnings,
        rule_confidence=rule_conf,
        nulled_fields=nulled,
    )
