"""Fundamental analyst.

Sees: yfinance valuation panel + 3-year financials/cashflow/balance sheet
(`FundamentalsExtended`) + sector/industry peer-median ratios
(`PeerComparison`).

Considers, in this order:
1. Revenue quality: growing / stable / declining; accelerating / decelerating.
2. Margin trajectory: expanding / stable / compressing; pricing power proxy.
3. Balance-sheet strength: current ratio, debt/equity, cash position.
4. Capital allocation: buybacks vs dilution vs capex-heavy reinvestment.
5. Peer comparison: forward P/E, PEG, growth, margins vs sector median.
6. Moat assessment (inferable from margin stability + premium margins +
   pricing power), explicitly flagged as inferable-vs-speculative.

Does NOT consider: chart technicals, news headlines, or creator claims.
Those are other lenses — keep this one clean so the user sees a standalone
"is this company actually worth what it costs" read.
"""

from __future__ import annotations

from app.market.fundamentals import FundamentalsExtended, RevenueTrajectory
from app.market.peer_comparison import PeerComparison
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
Your discipline is the company's actual business: how fast it grows, how \
profitably, how well-financed, and how it compares with the sector it lives \
in. You answer: 'is this a quality business at a sensible price?'

Hard rules:
1. Use ONLY the context provided. You do NOT see chart technicals or news \
   — other analysts cover those. If a number you'd want is missing, say so \
   and downweight conviction.
2. Always contextualise multiples against the peer-median when available. \
   'NVDA forward P/E 17 vs semis median 23' is the read; 'forward P/E 17' \
   alone is not.
3. Walk through the dimensions in your bullets (don't dump them all into \
   one sentence): revenue quality, margin trajectory, balance sheet, \
   capital allocation, peer comparison, moat. You don't have to hit every \
   dimension every time — skip the ones the data doesn't support.
4. Distinguish inferable from speculative. 'Margins steady at 60% suggests \
   pricing power' is inferable. 'It's the dominant chip designer' is fine \
   but mark as a qualitative read, not a number.
5. When peer set is unavailable, say so once and reason from absolute \
   numbers + the company's own trajectory.
6. NEVER use 'buy' / 'sell' / 'recommendation'. Use 'fairly valued', \
   'rich vs', 'cheap vs', 'consider', 'may', 'overpaying for', 'getting a \
   discount on'.
7. Submit exactly one lens via submit_lens. Summary ≤25 words. 2-4 points, \
   each grounded in a specific number from the context.
"""


USER_TEMPLATE = """\
Fundamental context for {ticker}{company_name} as of {as_of}.

Sector / industry: {sector} · {industry}

============================================================
VALUATION
============================================================
- market_cap: {market_cap}
- forward_PE / trailing_PE: {forward_pe} / {trailing_pe}
- PEG: {peg}
- P/S TTM / P/B / EV/EBITDA: {ps} / {pb} / {ev_ebitda}

============================================================
PEER COMPARISON ({peer_status})
============================================================
{peer_block}

============================================================
REVENUE TRAJECTORY (last 3 fiscal years, oldest → newest)
============================================================
{revenue_block}

============================================================
OPERATING MARGIN TRAJECTORY
============================================================
{op_margin_block}

============================================================
BALANCE SHEET (most recent)
============================================================
{balance_sheet_block}

============================================================
CASH FLOW & CAPITAL ALLOCATION
============================================================
{capital_block}

============================================================
CATALYST PROXIMITY
============================================================
- days_to_next_earnings: {days_to_earn}
- dividend_yield: {div_yield}
- beta: {beta}
- short_pct_of_float: {short_pct}
- held_pct_institutions: {inst_pct}

Submit your Fundamental lens via submit_lens. Cover at least 3 of the 6 \
dimensions (revenue quality, margins, balance sheet, capital allocation, \
peer comparison, moat) and cite specific numbers.
"""


def run_fundamental(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    val = packet.view.valuation
    fext = packet.fundamentals_extended
    pcmp = packet.peer_comparison
    user_msg = USER_TEMPLATE.format(
        ticker=packet.ticker,
        company_name=f" ({packet.company_name})" if packet.company_name else "",
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
        peer_status=_peer_status(pcmp),
        peer_block=_format_peer_block(val, pcmp),
        revenue_block=_format_revenue_block(fext),
        op_margin_block=_format_op_margin_block(fext, val),
        balance_sheet_block=_format_balance_sheet_block(fext),
        capital_block=_format_capital_block(fext, val),
        days_to_earn=val.days_to_next_earnings
        if val.days_to_next_earnings is not None
        else "—",
        div_yield=_fmt(val.dividend_yield, 2),
        beta=_fmt(val.beta, 2),
        short_pct=_fmt_pct(val.short_pct_of_float),
        inst_pct=_fmt_pct(val.held_pct_institutions),
    )
    return run_agent(
        agent_name="fundamental",
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        client=client,
        max_tokens=2000,
    )


def _peer_status(p: PeerComparison | None) -> str:
    if p is None:
        return "not fetched"
    if not p.peer_set_available:
        return p.note or "peer set unavailable"
    return f"{len(p.peer_tickers)} peers"


def _format_peer_block(val, pcmp: PeerComparison | None) -> str:
    if pcmp is None or not pcmp.peer_set_available:
        if pcmp and pcmp.note:
            return f"(no peer comparison — {pcmp.note})"
        return "(no peer comparison available)"
    peers = ", ".join(pcmp.peer_tickers)
    rows = [
        f"peers: {peers}",
        f"- forward P/E: target {_fmt(val.forward_pe, 1)} vs sector median "
        f"{_fmt(pcmp.median_forward_pe, 1)} → {pcmp.forward_pe_relative}",
        f"- PEG: target {_fmt(val.peg_ratio, 2)} vs sector median "
        f"{_fmt(pcmp.median_peg, 2)}",
        f"- P/S TTM: target {_fmt(val.price_to_sales_ttm, 1)} vs sector median "
        f"{_fmt(pcmp.median_price_to_sales, 1)}",
        f"- revenue growth YoY: target {_fmt_pct(val.revenue_growth_yoy)} vs sector "
        f"median {_fmt_pct(pcmp.median_revenue_growth_yoy)} → {pcmp.growth_relative}",
        f"- profit margins: target {_fmt_pct(val.profit_margins)} vs sector "
        f"median {_fmt_pct(pcmp.median_profit_margins)} → {pcmp.margin_relative}",
    ]
    return "\n".join(rows)


def _format_revenue_block(f: FundamentalsExtended | None) -> str:
    if f is None or f.revenue is None:
        return _missing_block(f, "revenue trajectory unavailable")
    r = f.revenue
    lines = [
        f"FY-2 → FY-1 → FY-0: {_fmt_dollars(r.fy_minus_2)} → "
        f"{_fmt_dollars(r.fy_minus_1)} → {_fmt_dollars(r.fy_minus_0)}",
        f"YoY recent: {_fmt_pct(r.yoy_recent)}, YoY prior: {_fmt_pct(r.yoy_prior)}",
        f"Quality read: {r.quality}",
    ]
    return "\n".join(lines)


def _format_op_margin_block(
    f: FundamentalsExtended | None, val
) -> str:
    if f is None or all(
        m is None
        for m in (
            f.operating_margin_fy_minus_2,
            f.operating_margin_fy_minus_1,
            f.operating_margin_fy_minus_0,
        )
    ):
        return _missing_block(
            f, f"operating margin history unavailable (TTM profit margin {_fmt_pct(val.profit_margins)})"
        )
    lines = [
        f"FY-2 → FY-1 → FY-0: {_fmt_pct(f.operating_margin_fy_minus_2)} → "
        f"{_fmt_pct(f.operating_margin_fy_minus_1)} → {_fmt_pct(f.operating_margin_fy_minus_0)}",
        f"Trajectory: {f.operating_margin_trajectory}",
        f"TTM profit margin (info): {_fmt_pct(val.profit_margins)}",
    ]
    return "\n".join(lines)


def _format_balance_sheet_block(f: FundamentalsExtended | None) -> str:
    if f is None:
        return "(not fetched)"
    if f.current_ratio is None and f.debt_to_equity is None and f.total_cash is None:
        return _missing_block(f, "balance sheet rows unavailable")
    lines = [
        f"current_ratio: {_fmt(f.current_ratio, 2)} "
        f"(>1.5 strong, 1-1.5 adequate, <1 stressed)",
        f"debt_to_equity: {_fmt(f.debt_to_equity, 2)} "
        f"(<0.5 light, 0.5-1.5 moderate, >1.5 levered)",
        f"total_cash: {_fmt_dollars(f.total_cash)}, total_debt: "
        f"{_fmt_dollars(f.total_debt)}",
        f"cash / market_cap: {_fmt_pct(f.cash_to_market_cap)}",
        f"Strength read: {f.balance_sheet_strength}",
    ]
    return "\n".join(lines)


def _format_capital_block(f: FundamentalsExtended | None, val) -> str:
    if f is None:
        return "(not fetched)"
    lines = [
        f"FCF (TTM): {_fmt_dollars(f.fcf_ttm)} → FCF yield "
        f"{_fmt_pct(f.fcf_yield)} of market cap",
        f"Capex (TTM): {_fmt_dollars(f.capex_ttm)} → "
        f"{_fmt_pct(f.capex_pct_revenue)} of revenue",
        f"Buyback yield (TTM cash repurchases / market cap): "
        f"{_fmt_pct(f.buyback_yield_ttm)}",
        f"Shares outstanding YoY change: "
        f"{_fmt_pct(f.shares_outstanding_yoy_pct)} (negative = buybacks net of "
        f"issuance, positive = dilution)",
        f"Dividend yield: {_fmt(val.dividend_yield, 2)}",
        f"Capital-allocation read: {f.capital_allocation}",
    ]
    return "\n".join(lines)


def _missing_block(f: FundamentalsExtended | None, fallback: str) -> str:
    if f is None:
        return "(not fetched)"
    if f.fetch_error:
        return f"(fetch error: {f.fetch_error})"
    return f"({fallback})"


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
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:.0f}"


REVISION_SYSTEM_PROMPT = """\
You are the Fundamental analyst on a four-lens swing-trading panel. You \
already submitted a round-1 read on valuation / growth / margins / balance \
sheet / capital allocation / peer comparison. Now you see the OTHER THREE \
analysts' round-1 reads. Your job: revise YOUR read if their evidence \
changes your analysis.
- Stay in your lane: forward earnings, multiples, margins, peer context, \
  balance sheet, capital allocation, moat read.
- DO NOT pivot into technicals or sentiment commentary — note their reads \
  in `responded_to` but keep your `revised_summary` and `revised_points` \
  rooted in fundamentals.
- It is FINE to leave direction / conviction unchanged; an honest \
  "considered the technical read but my fundamental concerns stand" is a \
  valid revision.
- Cite specific numbers from the packet (revenue YoY, margin trajectory, \
  current ratio, peer-median deltas, buyback yield) when relevant.
- `revised_summary`: ≤350 characters (~2 sentences). Tight, not verbose.
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
