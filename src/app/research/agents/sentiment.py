"""Sentiment / Macro analyst.

Sees: sector + industry tags, beta, recent creator claims, transcript context,
**plus one Anthropic web_search call** to pull recent news / outlook for the
ticker. Wraps creator sentiment as ONE input rather than the primary signal.

Considers:
1. Sector tailwinds vs headwinds (is the sector in or out of favor and why?)
2. Regulatory risk (antitrust, tariffs, compliance burden)
3. Geopolitical exposure (supply-chain concentration, sanctions, China/Taiwan)
4. Macro sensitivity (rates, commodities, consumer discretionary in a slowdown)
5. Recent catalysts surfaced via web search (earnings surprise, management
   change, M&A rumor, analyst upgrade/downgrade)
6. Creator sentiment as a supporting signal — absence of coverage is NOT
   bearish.

Does NOT consider: chart technicals, valuation multiples. Those are other
lenses — keep this one focused on context.

The web_search budget is 1 search per Deep run (toggleable via
`research.deep.sentiment_web_search` in settings.yaml). Adds ~$0.01.
"""

from __future__ import annotations

from app.config import load_project_settings
from app.research.agents.base import (
    AgentResult,
    _format_other_lenses,
    run_agent,
    run_agent_with_web_search,
    run_revision,
)
from app.research.context import ResearchPacket
from app.research.schema import LensView

SYSTEM_PROMPT = """\
You are the Sentiment-Macro analyst on a four-lens swing-trading research \
panel. Your discipline is the QUALITATIVE CONTEXT around the trade: sector \
positioning, regulatory and geopolitical exposure, macro sensitivity, recent \
catalysts in the news, and creator coverage as one signal among many. You \
answer: 'what is the environment around this name, and what could change it?'

You have an Anthropic web_search tool. Use it ONCE this turn to pull recent \
news / outlook for the ticker — earnings surprise, analyst moves, regulatory \
or geopolitical headlines, sector flow, M&A rumor. After the search, call \
submit_lens with your read.

Hard rules:
1. Sector tailwind/headwind read is the FIRST bullet whenever sector context \
   suggests a meaningful tilt. 'Semis bid on AI capex' / 'Solid-oxide fuel \
   cells out of favor after tax credit risk' is the shape.
2. Cover at least 2 of these dimensions per read: sector, regulatory, \
   geopolitical, macro sensitivity, recent catalyst (from search), creator \
   sentiment. Skip any dimension the context can't support.
3. Cite at least one specific finding from the web_search results when you \
   used it (e.g. 'Reuters 2026-05-22: Q2 beat by 4¢ on data-center demand'). \
   The reader needs to know what news anchored the read.
4. Creator sentiment is ONE input, not the headline. If there's no YouTube \
   coverage, say so neutrally — 'no recent creator coverage' is NOT bearish.
5. Distinguish factual catalyst (earnings beat) from speculation (could \
   surge). Factual carries weight; hype carries negative weight.
6. NEVER use 'buy' / 'sell' / 'recommendation'. Use 'environment supports', \
   'flow indicates', 'macro headwind', 'consider', 'may'.
7. Submit exactly one lens via submit_lens. Summary ≤25 words. 2-4 points.
"""


USER_TEMPLATE = """\
Sentiment / Macro context for {ticker}{company_name} as of {as_of}.

Sector / industry: {sector} · {industry}
Beta: {beta}   ·   Days to next earnings: {days_to_earn}

============================================================
SEARCH HINT (for your web_search call)
============================================================
Suggested query: "{search_query}"
Use web_search ONCE this turn (you have max_uses=1). Look for: earnings, \
analyst moves, regulatory / geopolitical headlines, sector flow, M&A rumor.

============================================================
CREATOR COVERAGE (one input — not the primary signal)
============================================================
- has_data: {has_data}
- coverage_status: {coverage_status}
- n_distinct_creators (across all claims): {n_creators}
- n_calibrated_creators: {n_calibrated}
- net_polarity_30d: {net_polarity}
- credibility_weighted_polarity_30d: {weighted_polarity}
- most_recent_mention_at: {most_recent} ({days_since}d old)
- agreement vs technical setup: {confirms}
- transcript summary: {summary}

Recent claims (last 30d, accepted only — {n_claims} claims):
{claims_block}

Submit your Sentiment / Macro lens via submit_lens AFTER your web_search \
call. Sector tilt, regulatory, geopolitical, macro sensitivity, recent \
catalysts from search, creator-sentiment-as-one-input — cover at least 2 \
dimensions and cite at least one search finding.
"""

# Pure-claims fallback template, used when web search is disabled or the
# initial search call errored out. Identical content sans the search hint.
USER_TEMPLATE_NO_SEARCH = """\
Sentiment / Macro context for {ticker}{company_name} as of {as_of}.

Sector / industry: {sector} · {industry}
Beta: {beta}   ·   Days to next earnings: {days_to_earn}

(web_search disabled this run — reason from sector, claims, and known \
catalyst proximity only.)

============================================================
CREATOR COVERAGE (one input — not the primary signal)
============================================================
- has_data: {has_data}
- coverage_status: {coverage_status}
- n_distinct_creators: {n_creators}
- n_calibrated_creators: {n_calibrated}
- net_polarity_30d: {net_polarity}
- credibility_weighted_polarity_30d: {weighted_polarity}
- most_recent_mention_at: {most_recent} ({days_since}d old)
- agreement vs technical setup: {confirms}
- transcript summary: {summary}

Recent claims ({n_claims}):
{claims_block}

Submit via submit_lens — cover sector tilt, regulatory or geopolitical \
exposure, macro sensitivity, and creator sentiment where supported.
"""


def run_sentiment(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    v = packet.view
    t = v.transcript
    val = v.valuation
    claims_block = _format_claims(packet) or "(no recent claims in lookback window)"

    settings = _safe_settings()
    web_search_enabled = bool(settings.sentiment_web_search)
    max_uses = max(0, int(settings.sentiment_web_search_max_uses))

    company = packet.company_name or ""
    search_query = _build_search_query(packet.ticker, company)

    common_kwargs = dict(
        ticker=packet.ticker,
        company_name=f" ({company})" if company else "",
        as_of=packet.as_of.isoformat(),
        sector=val.sector or v.identity.sector or "—",
        industry=val.industry or "—",
        beta=_fmt(val.beta, 2),
        days_to_earn=val.days_to_next_earnings
        if val.days_to_next_earnings is not None
        else "—",
        has_data="yes" if t.has_data else "no",
        coverage_status=t.coverage_status,
        n_creators=t.n_distinct_creators,
        n_calibrated=t.n_calibrated_creators,
        net_polarity=_fmt(t.net_polarity_30d, 2),
        weighted_polarity=_fmt(t.credibility_weighted_polarity_30d, 2),
        most_recent=t.most_recent_mention_at.isoformat()
        if t.most_recent_mention_at
        else "—",
        days_since=t.days_since_most_recent
        if t.days_since_most_recent is not None
        else "—",
        confirms=t.confirms_or_contradicts,
        summary=t.summary or "—",
        n_claims=len(packet.recent_claims),
        claims_block=claims_block,
    )

    if web_search_enabled and max_uses > 0:
        user_msg = USER_TEMPLATE.format(search_query=search_query, **common_kwargs)
        return run_agent_with_web_search(
            agent_name="sentiment_macro",
            system_prompt=SYSTEM_PROMPT,
            user_message=user_msg,
            web_search_max_uses=max_uses,
            client=client,
        )

    user_msg = USER_TEMPLATE_NO_SEARCH.format(**common_kwargs)
    return run_agent(
        agent_name="sentiment_macro",
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        client=client,
    )


def _safe_settings():
    try:
        return load_project_settings().research.deep
    except Exception:
        # Tests / no-config environments → defaults.
        from app.config import DeepResearchSettings
        return DeepResearchSettings()


def _build_search_query(ticker: str, company: str) -> str:
    if company:
        return f"{ticker} {company} news outlook 2026"
    return f"{ticker} stock news outlook 2026"


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
You already submitted a round-1 read on sector / regulatory / geopolitical \
/ macro / catalysts / creator coverage. Now you see the OTHER THREE \
analysts' round-1 reads. Your job: revise YOUR read if their evidence \
changes your analysis.
- Stay in your lane: macro and sentiment context. Do not pivot into pure \
  technicals or valuation multiples.
- Engage with whether the other lenses' reads alter the CONTEXT picture \
  (e.g. "Fundamental's margin-compression read amplifies the sector \
  headwind I flagged").
- It is FINE to stand pat with `responded_to=[]` if their reads don't bear \
  on the macro / sentiment picture.
- `revised_summary`: ≤350 characters (~2 sentences). Tight.
- `revised_points`: 1-4 bullets, each ≤120 characters.
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
