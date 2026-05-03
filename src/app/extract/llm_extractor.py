"""Claude-based LLM extractor.

Wraps Anthropic tool-use to produce a strict, schema-validated `LLMExtractedCall`
or `NoCallFound`. The model is given two tools — exactly one must be called per
candidate window. Two-pass mode is supported via `extract_with_validation()`,
which asks the model to re-check its own extraction against the source text.

Designed to be unit-testable: a `_call_messages_api` seam lets tests inject a
canned response without hitting the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic

from app.config import load_env
from app.extract.schemas import (
    CALL_TOOL_DESCRIPTION,
    CALL_TOOL_NAME,
    NO_CALL_TOOL_DESCRIPTION,
    NO_CALL_TOOL_NAME,
    LLMExtractedCall,
    NoCallFound,
    call_tool_input_schema,
    no_call_tool_input_schema,
)
from app.logging import get_logger

log = get_logger(__name__)


SYSTEM_PROMPT = """\
You extract structured stock-trade calls from messy YouTube transcripts. \
You are forensic, not creative.

Hard rules:
1. NEVER invent fields. If the speaker did not state a stop-loss, set stop_price=null. \
   Same for entry_price, target_price, and timeframe.
2. EVERY non-null field requires a verbatim `*_evidence` quote from the source text. \
   The quote must appear in the source character-for-character (modulo whitespace).
3. Use submit_no_call_found if the source is general market commentary, vague \
   sector talk, an interview, or anything without a clear directional view on \
   a specific ticker.
4. Set direction = "unspecified" only when the speaker mentions a ticker but is \
   genuinely undecided. A bullish framing without a price level is still direction = "long".
5. entry_type semantics:
   - "market": speaker says they're entering now / next open ("buying here", "long here").
   - "limit": passive fill at a stated price ("looking to buy at 195").
   - "trigger_above": conditional entry on upside cross ("above 920", "if it reclaims 920").
   - "trigger_below": conditional entry on downside cross ("below 920").
   - "unspecified": directional view with no entry mechanism stated.
6. Calibrate `overall_confidence` honestly. A vague mention with no price = ~0.4. \
   A clear ticker + direction + price level + reasoning = ~0.85+.
"""


USER_TEMPLATE = """\
Source text (consolidated transcript window):
\"\"\"
{text}
\"\"\"

Extract any actionable swing-trade call from the source. Use the appropriate tool.
"""


VALIDATION_TEMPLATE = """\
You previously extracted this call from the source below. Re-check each non-null field: \
is it actually supported by the source text, character-for-character (modulo whitespace)? \
If a field's evidence is fabricated, paraphrased, or weak, drop that field (set to null + \
remove its evidence). Then resubmit the corrected call. If after correction the call has no \
ticker or no direction, call submit_no_call_found instead.

Source:
\"\"\"
{text}
\"\"\"

Previous extraction:
{previous_json}
"""


@dataclass
class ExtractionResult:
    call: LLMExtractedCall | None
    no_call: NoCallFound | None
    raw_response: dict[str, Any]

    @property
    def is_call(self) -> bool:
        return self.call is not None


class _MessagesAPILike(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


class LLMExtractor:
    """Claude-backed extractor. Construct once per process; thread-safe."""

    def __init__(
        self,
        *,
        client: Anthropic | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
    ) -> None:
        env = load_env()
        if client is None:
            if not env.anthropic_api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set; cannot construct LLMExtractor without one."
                )
            client = Anthropic(api_key=env.anthropic_api_key)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._tools = [
            {
                "name": CALL_TOOL_NAME,
                "description": CALL_TOOL_DESCRIPTION,
                "input_schema": call_tool_input_schema(),
            },
            {
                "name": NO_CALL_TOOL_NAME,
                "description": NO_CALL_TOOL_DESCRIPTION,
                "input_schema": no_call_tool_input_schema(),
            },
        ]

    def extract(self, text: str) -> ExtractionResult:
        """One-pass extraction. Use extract_with_validation() for the safer two-pass."""
        messages = [{"role": "user", "content": USER_TEMPLATE.format(text=text)}]
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            tools=self._tools,
            tool_choice={"type": "any"},
            messages=messages,
        )
        return self._parse_response(response)

    def extract_with_validation(self, text: str) -> ExtractionResult:
        """Two-pass: extract, then ask the model to re-validate its own output."""
        first = self.extract(text)
        if first.call is None:
            return first

        previous_json = first.call.model_dump_json(indent=2)
        messages = [
            {
                "role": "user",
                "content": VALIDATION_TEMPLATE.format(text=text, previous_json=previous_json),
            }
        ]
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            tools=self._tools,
            tool_choice={"type": "any"},
            messages=messages,
        )
        return self._parse_response(response)

    # -- helpers --------------------------------------------------------

    def _parse_response(self, response: Any) -> ExtractionResult:
        raw_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = block.name
            data = block.input
            if name == CALL_TOOL_NAME:
                try:
                    call = LLMExtractedCall.model_validate(data)
                except Exception as e:
                    log.warning("extract.llm.schema_validation_failed", error=str(e))
                    return ExtractionResult(call=None, no_call=None, raw_response=raw_dict)
                return ExtractionResult(call=call, no_call=None, raw_response=raw_dict)
            if name == NO_CALL_TOOL_NAME:
                try:
                    no_call = NoCallFound.model_validate(data)
                except Exception as e:
                    log.warning("extract.llm.no_call_schema_failed", error=str(e))
                    return ExtractionResult(call=None, no_call=None, raw_response=raw_dict)
                return ExtractionResult(call=None, no_call=no_call, raw_response=raw_dict)

        log.warning("extract.llm.no_tool_use", stop_reason=getattr(response, "stop_reason", None))
        return ExtractionResult(call=None, no_call=None, raw_response=raw_dict)
