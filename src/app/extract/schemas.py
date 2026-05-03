"""Pydantic schemas for the LLM extractor's structured output.

These define the JSON Schema that the Claude tool-use call must conform to.
They are deliberately strict: missing fields must be `null`, every non-null
field requires an `evidence_quote`, and the model output gets validated here
before any downstream rule-validator runs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Direction = Literal["long", "short", "unspecified"]
EntryType = Literal["market", "limit", "trigger_above", "trigger_below", "unspecified"]
Timeframe = Literal["day", "swing", "position", "unspecified"]


class NoCallFound(BaseModel):
    """The LLM returns this when the window contains no actionable trade call."""

    is_trade_call: Literal[False] = False
    reason: str = Field(description="Brief reason why no call was found.")


class LLMExtractedCall(BaseModel):
    """Structured trade call output from the LLM extractor.

    Required: ticker, direction, entry_type, overall_confidence.
    Optional but encouraged: target_price, stop_price, timeframe,
                              reasoning_summary, entry_price (when applicable).

    Every non-null field with an associated `*_evidence` slot MUST carry the
    verbatim source quote. The validator enforces this.
    """

    is_trade_call: Literal[True] = True

    ticker: str = Field(description="Ticker symbol, uppercase, no $ prefix. e.g. NVDA.")
    ticker_evidence: str = Field(
        description="Verbatim phrase from the source that names the ticker."
    )

    direction: Direction = Field(
        description=(
            "long if speaker expects price to rise, short if to fall, "
            "unspecified if direction is genuinely ambiguous."
        )
    )
    direction_evidence: str = Field(
        description="Verbatim phrase that establishes the directional intent."
    )

    entry_type: EntryType = Field(
        description=(
            "market = enter immediately at next session; "
            "limit = enter at a specific price (passive fill); "
            "trigger_above / trigger_below = conditional entry on price crossing a level; "
            "unspecified = directional view with no entry mechanism stated."
        )
    )
    entry_price: float | None = Field(
        default=None,
        description=(
            "Entry / trigger / limit price. Required when entry_type is "
            "limit / trigger_above / trigger_below; otherwise null."
        ),
    )
    entry_evidence: str | None = Field(
        default=None,
        description="Verbatim source quote justifying entry_price. Null iff entry_price is null.",
    )

    target_price: float | None = Field(default=None)
    target_evidence: str | None = Field(default=None)

    stop_price: float | None = Field(default=None)
    stop_evidence: str | None = Field(default=None)

    timeframe: Timeframe = Field(default="unspecified")
    timeframe_evidence: str | None = Field(default=None)

    reasoning_summary: str | None = Field(
        default=None,
        description="One-line distillation of the speaker's stated reasoning. Optional.",
    )

    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Self-reported confidence that this is a real, actionable call as "
            "described. Calibrate honestly: 0.95 means 'I would bet money this "
            "is a clear directional call from the speaker', 0.5 means 'plausible "
            "but I have real doubts'."
        ),
    )

    @model_validator(mode="after")
    def _evidence_must_match_value_presence(self) -> LLMExtractedCall:
        """Reject obvious self-contradictions before the rule validator runs."""
        pairs = [
            ("entry_price", "entry_evidence"),
            ("target_price", "target_evidence"),
            ("stop_price", "stop_evidence"),
        ]
        for value_field, evidence_field in pairs:
            v = getattr(self, value_field)
            e = getattr(self, evidence_field)
            if v is not None and not e:
                raise ValueError(
                    f"{value_field} is set but {evidence_field} is missing — "
                    "non-null value fields require an evidence quote."
                )
            if v is None and e:
                raise ValueError(
                    f"{evidence_field} is provided but {value_field} is null — "
                    "drop the evidence if the value isn't extracted."
                )

        # entry_price required for limit / trigger_*
        if self.entry_type in {"limit", "trigger_above", "trigger_below"} and self.entry_price is None:
            raise ValueError(
                f"entry_type={self.entry_type!r} requires entry_price to be set."
            )

        return self


# --- JSON Schema for Claude tool-use input_schema ---------------------------

CALL_TOOL_NAME = "submit_extracted_call"
CALL_TOOL_DESCRIPTION = (
    "Submit a structured trade call extracted from the source text. "
    "Set is_trade_call=false (via the no_call tool) only when the source "
    "contains no actionable directional trade call."
)

NO_CALL_TOOL_NAME = "submit_no_call_found"
NO_CALL_TOOL_DESCRIPTION = (
    "Submit when the source contains no actionable directional trade call. "
    "Use this for general market commentary, sector talk, or videos that "
    "discuss tickers without a clear directional view."
)


def call_tool_input_schema() -> dict:
    """JSON Schema fed to Claude tool-use for the extracted-call tool."""
    schema = LLMExtractedCall.model_json_schema()
    # Remove pydantic-only metadata that confuses some clients.
    schema.pop("title", None)
    return schema


def no_call_tool_input_schema() -> dict:
    schema = NoCallFound.model_json_schema()
    schema.pop("title", None)
    return schema
