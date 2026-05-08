"""Fundamental analyst.

Sees: valuation panel + sector/industry context.
Considers: forward earnings trajectory, valuation vs sector and vs growth,
margins, balance sheet proxies (P/B, EV/EBITDA), revenue momentum.

Does NOT consider: technicals, news, or sentiment claims. Those are other
lenses — keep this one clean so the user can see "is this expensive or
cheap relative to its growth" as a standalone read.
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
You are the Fundamental analyst on a four-lens swing-trading research panel. \
Your discipline is valuation, growth, margin, and sector context. You answer: \
'is this fairly priced for what it's growing into?'

Hard rules:
1. Use ONLY the valuation block in the user message. You do NOT see chart \
   technicals or news — other analysts cover those.
2. Acknowledge sector context: a 30× forward P/E is rich for utilities, \
   normal for software. PEG > 2 means the multiple is rich vs growth; PEG < \
   1 means cheap vs growth. Cite the actual numbers.
3. If valuation data is missing (likely an ETF or pre-IPO ticker), say so \
   plainly and emit conviction='low' with direction='neutral'.
4. NEVER use 'buy' / 'sell' / 'recommendation'. Use 'fairly valued', \
   'rich vs', 'cheap vs', 'consider', 'may'.
5. Submit exactly one lens via submit_lens.
"""


USER_TEMPLATE = """\
Fundamental context for {ticker} as of {as_of}.

Sector / industry: {sector} · {industry}

Valuation:
- market_cap: {market_cap}
- forward_PE / trailing_PE: {forward_pe} / {trailing_pe}
- PEG: {peg}
- P/S TTM / P/B / EV/EBITDA: {ps} / {pb} / {ev_ebitda}

Growth & margins:
- earnings_growth_forward: {eps_fwd}
- revenue_growth_yoy: {rev_yoy}
- profit_margins: {margins}

Float / shorts / risk:
- float_shares / shares_out: {float_shares} / {shares_out}
- short_pct_of_float: {short_pct}
- held_pct_institutions: {inst_pct}
- beta: {beta}

Catalyst proximity:
- days_to_next_earnings: {days_to_earn}
- dividend_yield: {div_yield}

Submit your Fundamental lens via submit_lens.
"""


def run_fundamental(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    val = packet.view.valuation
    user_msg = USER_TEMPLATE.format(
        ticker=packet.ticker,
        as_of=packet.as_of.isoformat(),
        sector=val.sector or "—",
        industry=val.industry or "—",
        market_cap=_fmt_dollars(val.market_cap),
        forward_pe=_fmt(val.forward_pe, 1),
        trailing_pe=_fmt(val.trailing_pe, 1),
        peg=_fmt(val.peg_ratio, 2),
        ps=_fmt(val.price_to_sales_ttm, 1),
        pb=_fmt(val.price_to_book, 2),
        ev_ebitda=_fmt(val.enterprise_to_ebitda, 1),
        eps_fwd=_fmt_pct(val.earnings_growth_forward),
        rev_yoy=_fmt_pct(val.revenue_growth_yoy),
        margins=_fmt_pct(val.profit_margins),
        float_shares=_fmt_count(val.float_shares),
        shares_out=_fmt_count(val.shares_outstanding),
        short_pct=_fmt_pct(val.short_pct_of_float),
        inst_pct=_fmt_pct(val.held_pct_institutions),
        beta=_fmt(val.beta, 2),
        days_to_earn=val.days_to_next_earnings if val.days_to_next_earnings is not None else "—",
        div_yield=_fmt(val.dividend_yield, 2),
    )
    return run_agent(
        agent_name="fundamental",
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        client=client,
    )


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_pct(value) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


def _fmt_dollars(v) -> str:
    if v is None:
        return "—"
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:.0f}"


def _fmt_count(v) -> str:
    if v is None:
        return "—"
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    return f"{v:.0f}"


REVISION_SYSTEM_PROMPT = """\
You are the Fundamental analyst on a four-lens swing-trading panel. You \
already submitted a round-1 read on valuation / growth / sector context. \
Now you see the OTHER THREE analysts' round-1 reads. Your job: revise YOUR \
read if their evidence changes your analysis.
- Stay in your lane: forward earnings, valuation multiples, sector / \
  industry context, growth quality, margin trajectory.
- DO NOT pivot into technicals or sentiment commentary — note their reads \
  in `responded_to` but keep your `revised_summary` and `revised_points` \
  rooted in fundamentals.
- It is FINE to leave direction / conviction unchanged; an honest \
  "considered the technical read but my fundamental concerns stand" is a \
  valid revision.
- Cite specific numbers from the packet when relevant.
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


def run_fundamental_revision(
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
        agent_name="fundamental",
        system_prompt=REVISION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        round_one_lens=round_one_lens,
        client=client,
    )


__all__ = ["run_fundamental", "run_fundamental_revision"]
