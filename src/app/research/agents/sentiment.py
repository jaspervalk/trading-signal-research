"""Sentiment / Macro analyst.

Sees: recent claims (creator coverage), transcript context, identity
(sector for macro tagging).
Considers: creator narrative momentum, claim polarity & class
distribution, catalyst proximity (earnings + creator-flagged events),
sector RS, macro regime hints.

Does NOT consider: pure technicals (RSI, ATR), valuation. Other lenses
own those — and conflating them here would dilute the signal.

This is OUR moat: the YouTube creator-claim corpus is unique to this
project. Make the most of it.
"""

from __future__ import annotations

from app.research.agents.base import (
    AgentResult,
    _format_other_lenses,
    run_agent,
    run_revision,
)
from app.research.context import ResearchPacket
from app.research.schema import LensView

SYSTEM_PROMPT = """\
You are the Sentiment / Macro analyst on a four-lens swing-trading research \
panel. Your discipline is creator narrative momentum, claim polarity, and \
catalyst / regime context. You answer: 'what story are people telling about \
this ticker right now, how strong is it, and what would change it?'

Hard rules:
1. Use ONLY the claims and transcript context in the user message. You do \
   NOT see chart technicals or valuation — other analysts cover those.
2. Cite specific creator claims (creator + date + class + polarity) when \
   making points. Generic 'sentiment is positive' is insufficient.
3. Distinguish factual catalysts (earnings beat) from speculation (could \
   surge). Factual claims carry more weight; hype carries negative weight.
4. Note claim freshness — if the most recent mention is > 90 days old, \
   call it 'historical only' and reduce conviction.
5. NEVER use 'buy' / 'sell' / 'recommendation'. Use 'narrative supports', \
   'flow indicates', 'consider'.
6. Submit exactly one lens via submit_lens.
"""


USER_TEMPLATE = """\
Sentiment / Macro context for {ticker} as of {as_of}.

Sector: {sector}

Transcript coverage summary:
- has_data: {has_data}
- coverage_status: {coverage_status}
- n_distinct_creators (across all claims): {n_creators}
- n_calibrated_creators: {n_calibrated}
- net_polarity_30d: {net_polarity}
- credibility_weighted_polarity_30d: {weighted_polarity}
- most_recent_mention_at: {most_recent} ({days_since}d old)
- agreement vs technical setup: {confirms}
- summary: {summary}

Recent claims (last 30d, accepted only — {n_claims} claims):
{claims_block}

Submit your Sentiment / Macro lens via submit_lens.
"""


def run_sentiment(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    v = packet.view
    t = v.transcript
    claims_block = _format_claims(packet) or "(no recent claims in lookback window)"
    user_msg = USER_TEMPLATE.format(
        ticker=packet.ticker,
        as_of=packet.as_of.isoformat(),
        sector=v.identity.sector or "—",
        has_data="yes" if t.has_data else "no",
        coverage_status=t.coverage_status,
        n_creators=t.n_distinct_creators,
        n_calibrated=t.n_calibrated_creators,
        net_polarity=_fmt(t.net_polarity_30d, 2),
        weighted_polarity=_fmt(t.credibility_weighted_polarity_30d, 2),
        most_recent=t.most_recent_mention_at.isoformat() if t.most_recent_mention_at else "—",
        days_since=t.days_since_most_recent if t.days_since_most_recent is not None else "—",
        confirms=t.confirms_or_contradicts,
        summary=t.summary or "—",
        n_claims=len(packet.recent_claims),
        claims_block=claims_block,
    )
    return run_agent(
        agent_name="sentiment_macro",
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        client=client,
    )


def _format_claims(p: ResearchPacket) -> str:
    if not p.recent_claims:
        return ""
    parts: list[str] = []
    for c in p.recent_claims:
        creator = c.creator_name or "unknown"
        parts.append(
            f"- [{c.claim_type}/{c.claim_class}/{c.polarity}] "
            f"{c.summary} ({creator}, {c.posted_at.date()}, conf={c.final_confidence:.2f})"
        )
    return "\n".join(parts)


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


REVISION_SYSTEM_PROMPT = """\
You are the Sentiment-Macro analyst on a four-lens swing-trading panel. \
You already submitted a round-1 read on creator claims / flow / catalysts \
/ macro regime. Now you see the OTHER THREE analysts' round-1 reads. Your \
job: revise YOUR read if their evidence changes your analysis.
- Stay in your lane: claims, positioning, catalyst proximity, macro tone. \
  Do not pivot into pure technicals or valuation.
- Engage with whether the other lenses' reads alter the SENTIMENT picture \
  (e.g. "Quant's bullish breakout aligns with my creator-flow read; \
  conviction reinforced").
- It is FINE to stand pat with `responded_to=[]` if their reads don't \
  bear on sentiment.
- Submit via submit_revised_lens.
"""


REVISION_USER_TEMPLATE = """\
Your round-1 read:
- direction: {round_one_direction}
- conviction: {round_one_conviction}
- summary: {round_one_summary}
- points:
{round_one_points}

OTHER LENSES (round 1):
{others_block}

Revise your read via submit_revised_lens.
"""


def run_sentiment_revision(
    packet: ResearchPacket,
    *,
    round_one_lens: LensView,
    others: list[LensView],
    client=None,
) -> AgentResult:
    user_prompt = REVISION_USER_TEMPLATE.format(
        round_one_direction=round_one_lens.direction,
        round_one_conviction=round_one_lens.conviction,
        round_one_summary=round_one_lens.summary,
        round_one_points="\n".join(f"- {p}" for p in round_one_lens.points),
        others_block=_format_other_lenses(others),
    )
    return run_revision(
        agent_name="sentiment_macro",
        system_prompt=REVISION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        round_one_lens=round_one_lens,
        client=client,
    )


__all__ = ["run_sentiment", "run_sentiment_revision"]
