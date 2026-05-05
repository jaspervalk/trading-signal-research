"""Claude-based LLM extractor.

Wraps Anthropic tool-use to produce strict, schema-validated outputs:
  - `LLMExtractedCall` (a structured trade call), and/or
  - `LLMClaimsBatch` (zero-or-more non-trade-call claims per ADR 0006), and/or
  - `NoCallFound` (no call AND no claims worth recording).

The model receives three tools and may invoke any combination per candidate
window. Two-pass mode (`extract_with_validation`) re-checks the trade call's
evidence; claim re-validation is single-pass for V1 (re-prompting claims is
deferred — see ADR 0006 §"Anti-hallucination devices").

Designed to be unit-testable: tests inject a canned Anthropic-shape response
without hitting the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from anthropic import Anthropic

from app.config import load_env
from app.extract.schemas import (
    CALL_TOOL_DESCRIPTION,
    CALL_TOOL_NAME,
    CLAIMS_TOOL_DESCRIPTION,
    CLAIMS_TOOL_NAME,
    NO_CALL_TOOL_DESCRIPTION,
    NO_CALL_TOOL_NAME,
    LLMClaimsBatch,
    LLMExtractedCall,
    LLMExtractedClaim,
    NoCallFound,
    call_tool_input_schema,
    claims_tool_input_schema,
    no_call_tool_input_schema,
)
from app.logging import get_logger

log = get_logger(__name__)


SYSTEM_PROMPT = """\
You extract structured information from messy YouTube transcripts about stocks \
and markets. You are forensic, not creative. You may emit (a) one trade call, \
(b) zero or more claims, (c) both, or (d) neither.

Tools available:
- submit_extracted_call: emit ONE actionable trade call (ticker + direction + \
  optional entry/target/stop). Call this at most once per source window.
- submit_claims: emit ZERO OR MORE claims (catalyst, risk, earnings view, macro \
  theme, sector view, factual assertion, opinion, speculation, hype). Use this \
  for any noteworthy directional or factual statement that is NOT a trade call.
- submit_no_call_found: call ONLY when the source has no trade call AND no \
  notable claims. If you have any claims, use submit_claims instead.

Hard rules (apply to BOTH calls and claims):
1. NEVER invent fields. If the speaker did not state a stop-loss, set stop_price=null. \
   Same for entry_price, target_price, timeframe, and every claim field.
2. EVERY non-null field with an evidence slot requires a VERBATIM evidence quote \
   from the source. The quote must appear character-for-character (modulo whitespace). \
   This includes claim.evidence_quote.
3. Set direction = "unspecified" only when the speaker mentions a ticker but is \
   genuinely undecided. A bullish framing without a price level is still direction = "long".
4. entry_type semantics:
   - "market": speaker says they're entering now / next open ("buying here", "long here").
   - "limit": passive fill at a stated price ("looking to buy at 195").
   - "trigger_above": conditional entry on upside cross ("above 920", "if it reclaims 920").
   - "trigger_below": conditional entry on downside cross ("below 920").
   - "unspecified": directional view with no entry mechanism stated.
5. Calibrate `overall_confidence` honestly. A vague mention with no price = ~0.4. \
   A clear ticker + direction + price level + reasoning = ~0.85+.

Claim-specific rules:
6. claim_class is orthogonal to claim_type. A "catalyst" can be "factual" (e.g., \
   reported revenue beat) OR "speculation" (e.g., "I think AI demand will surge"). \
   If the evidence quote contains hedging language ("I think", "could", "might", \
   "probably", "likely"), the class is NEVER "factual" — use "opinion" or "speculation".
7. claim_type guidance:
   - catalyst / risk: a specific named driver. Requires ticker OR sector.
   - earnings_view: directional view on an upcoming print. Requires ticker.
   - macro_theme: broad regime claim. ticker may be null.
   - sector_view: directional view on a sector. Requires sector or ticker.
   - factual_assertion: a checkable fact (revenue, contract, ratings change).
   - opinion: stated preference, no falsifiable prediction.
   - speculation: directional guess without evidence.
   - hype: promotional/emotional language ("this stock is going to the moon").
8. Claim summary is YOUR canonicalization in <=20 words. The evidence_quote is \
   the verbatim source phrase.
"""


USER_TEMPLATE = """\
Source text (consolidated transcript window):
\"\"\"
{text}
\"\"\"

Extract:
1. Any actionable swing-trade call (call submit_extracted_call at most once).
2. Any noteworthy claims (catalysts, risks, earnings views, macro themes, etc.) \
   via submit_claims (pass an empty list if there are no claims to record).

Use submit_no_call_found ONLY if both are empty.
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
    """Outcome of one LLM extraction pass.

    A single pass may produce a call, a list of claims, both, or neither.
    `no_call` is set only when the model explicitly invoked submit_no_call_found
    AND emitted nothing else.
    """

    call: LLMExtractedCall | None = None
    claims: list[LLMExtractedClaim] = field(default_factory=list)
    no_call: NoCallFound | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def is_call(self) -> bool:
        return self.call is not None

    @property
    def has_claims(self) -> bool:
        return len(self.claims) > 0

    @property
    def is_empty(self) -> bool:
        """True when nothing actionable came out of this window."""
        return self.call is None and not self.claims


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
                "name": CLAIMS_TOOL_NAME,
                "description": CLAIMS_TOOL_DESCRIPTION,
                "input_schema": claims_tool_input_schema(),
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
        """Two-pass: extract, then ask the model to re-validate the trade call.

        Pass 2 only re-checks the call — claims from pass 1 are preserved as-is.
        Per ADR 0006, claim re-validation via prompting is deferred for V1; the
        rule-based validator + evidence-substring check is the primary defense
        against hallucinated claims.
        """
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
        second = self._parse_response(response)
        # Preserve pass-1 claims; pass 2 only adjudicates the call.
        return ExtractionResult(
            call=second.call,
            claims=first.claims,
            no_call=second.no_call if second.call is None else None,
            raw_response=second.raw_response,
        )

    # -- helpers --------------------------------------------------------

    def _parse_response(self, response: Any) -> ExtractionResult:
        """Collect ALL tool_use blocks. The model may emit a call AND claims in
        the same response; do not return on the first match."""
        raw_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        result = ExtractionResult(raw_response=raw_dict)

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = block.name
            data = block.input

            if name == CALL_TOOL_NAME:
                try:
                    result.call = LLMExtractedCall.model_validate(data)
                except Exception as e:
                    log.warning("extract.llm.call_schema_failed", error=str(e))
                continue

            if name == CLAIMS_TOOL_NAME:
                try:
                    batch = LLMClaimsBatch.model_validate(data)
                    result.claims.extend(batch.claims)
                except Exception as e:
                    # Salvage: try parsing each claim independently so one bad
                    # claim doesn't drop the whole batch.
                    log.warning("extract.llm.claims_batch_failed", error=str(e))
                    raw_claims = data.get("claims", []) if isinstance(data, dict) else []
                    for raw in raw_claims:
                        try:
                            result.claims.append(LLMExtractedClaim.model_validate(raw))
                        except Exception as ce:
                            log.warning("extract.llm.claim_schema_failed", error=str(ce))
                continue

            if name == NO_CALL_TOOL_NAME:
                try:
                    result.no_call = NoCallFound.model_validate(data)
                except Exception as e:
                    log.warning("extract.llm.no_call_schema_failed", error=str(e))
                continue

        if result.is_empty and result.no_call is None:
            log.warning(
                "extract.llm.no_tool_use",
                stop_reason=getattr(response, "stop_reason", None),
            )
        return result
