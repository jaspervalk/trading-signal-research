"""Contrarian / Risk analyst.

Sees: ALL packet content (full context) — this is the only lens that
reads cross-domain. Job: be the dissenting voice. Even on a "good" setup,
find the bear case.

Considers:
- What single observation would falsify the bull thesis?
- Crowded-trade / consensus signals (everyone bullish = positioning risk)
- Downside scenario in R units (if stop hits, what's the typical magnitude
  for this volatility profile?)
- Tail-risk catalysts (earnings within window, macro events, sector rotation)
- Hidden negatives in fundamentals (margin compression, slowing growth)

This lens is intentionally biased toward bearish reads. The other three
analysts will independently land bullish or neutral as the data warrants;
this one's job is to make sure no obvious bear case gets glossed over.
"""

from __future__ import annotations

from app.research.agents.base import AgentResult, run_agent
from app.research.context import ResearchPacket

SYSTEM_PROMPT = """\
You are the Contrarian / Risk analyst on a four-lens swing-trading research \
panel. The other three analysts (Quantitative, Fundamental, Sentiment-Macro) \
will independently land where the data lands. YOUR job is different: \
adversarial scrutiny. What could go wrong? What single observation would \
falsify the bull thesis? Where is the trade crowded?

Hard rules:
1. You see the full packet (technicals + valuation + claims + setup). USE all \
   of them — pull the strongest cross-domain bear signal, not just the \
   weakest indicator in any one block.
2. Always identify (a) the single observation that would falsify the trade, \
   (b) the downside magnitude if the stop fires (in R units or % terms), \
   (c) one crowding / consensus risk if you can find one.
3. If the setup genuinely looks great and you can find no real bear case, \
   say so explicitly and emit direction='neutral' or 'bullish' with \
   conviction='low'. DO NOT manufacture a bear case where none exists.
4. NEVER use 'buy' / 'sell' / 'recommendation'. Use 'risk', 'falsifies', \
   'crowded', 'asymmetric'.
5. Submit exactly one lens via submit_lens.
"""


USER_TEMPLATE = """\
Contrarian / Risk context for {ticker} as of {as_of}.

Setup summary (deterministic): {status} · {setup_type} · {style}
Status confidence: {status_conf}
Action label (derived): {action_label} — {action_derivation}

Technicals (you may cite any of these):
- RSI: {rsi}, ATR%: {atr_pct}, MA stack: {ma_alignment}
- 5d / 21d / 63d returns: {r5} / {r21} / {r63}
- pct off 52w high / low: {off_high} / {off_low}
- pullback from recent high: {pullback}, breakout distance: {breakout}
- vs SPY 21d / 63d: {ex21} / {rs63}

Valuation (you may cite any of these):
- forward_PE / trailing_PE: {fwd_pe} / {ttm_pe}
- PEG: {peg}, P/S: {ps}
- rev growth YoY: {rev_yoy}, EPS growth fwd: {eps_fwd}
- profit_margins: {margins}, beta: {beta}
- short_pct_of_float: {short_pct}
- days_to_next_earnings: {days_to_earn}

Sentiment summary:
- {n_claims} claims in last 30d
- coverage_status: {coverage_status}
- agreement with technical setup: {confirms}

Submit your Contrarian / Risk lens via submit_lens.
"""


def run_contrarian(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    v = packet.view
    val = v.valuation
    user_msg = USER_TEMPLATE.format(
        ticker=packet.ticker,
        as_of=packet.as_of.isoformat(),
        status=v.status.status,
        status_conf=v.status.confidence,
        setup_type=v.setup.setup_type,
        style=v.style_fit.primary_style or "—",
        action_label=v.action.label,
        action_derivation=v.action.derivation,
        rsi=_fmt(v.indicators.rsi_14, 0),
        atr_pct=_fmt_pct(v.indicators.atr_14_pct),
        ma_alignment=v.indicators.ma_alignment,
        r5=_fmt_pct(v.market.return_5d),
        r21=_fmt_pct(v.market.return_21d),
        r63=_fmt_pct(v.market.return_63d),
        off_high=_fmt_pct(v.market.pct_off_52w_high),
        off_low=_fmt_pct(v.market.pct_off_52w_low),
        pullback=_fmt_pct(v.levels.pullback_pct_from_recent_high),
        breakout=_fmt_pct(v.levels.breakout_distance_pct),
        ex21=_fmt_pct(v.market.excess_return_21d),
        rs63=_fmt_pct(v.indicators.relative_strength_vs_spy_63d),
        fwd_pe=_fmt(val.forward_pe, 1),
        ttm_pe=_fmt(val.trailing_pe, 1),
        peg=_fmt(val.peg_ratio, 2),
        ps=_fmt(val.price_to_sales_ttm, 1),
        rev_yoy=_fmt_pct(val.revenue_growth_yoy),
        eps_fwd=_fmt_pct(val.earnings_growth_forward),
        margins=_fmt_pct(val.profit_margins),
        beta=_fmt(val.beta, 2),
        short_pct=_fmt_pct(val.short_pct_of_float),
        days_to_earn=val.days_to_next_earnings if val.days_to_next_earnings is not None else "—",
        n_claims=len(packet.recent_claims),
        coverage_status=v.transcript.coverage_status,
        confirms=v.transcript.confirms_or_contradicts,
    )
    return run_agent(
        agent_name="contrarian_risk",
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


__all__ = ["run_contrarian"]
