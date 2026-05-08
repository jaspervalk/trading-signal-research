"""Quantitative / Technical analyst.

Sees: indicators, levels, market snapshot, candidate levels (technicals only).
Considers: trend regime, momentum, mean-reversion vs continuation, volatility
profile, RS-vs-SPY, breakout / pullback structure, factor-style positioning.

Does NOT consider: fundamentals (forward P/E etc.), news, sentiment claims.
The Fundamental and Sentiment-Macro analysts cover those; collapsing them
here would give the user one big mush instead of four independent reads.
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
You are the Quantitative analyst on a four-lens swing-trading research panel. \
Your discipline is statistical and price-based: trend regime, momentum vs \
mean-reversion, volatility positioning, relative strength, and breakout / \
pullback chart structure.

Hard rules:
1. Use ONLY the technicals block in the user message. You do NOT see \
   fundamentals, news, or creator claims — other analysts cover those, and \
   reaching outside your scope crowds them out.
2. Be specific: cite RSI / ATR / slope / RS / 5d-21d-63d returns. \
   Generic phrases like 'momentum looks strong' are insufficient.
3. NEVER use 'buy' / 'sell' / 'recommendation' / 'guarantee'. Use \
   'consider', 'may', 'favours', 'aligns with'.
4. Submit exactly one lens via submit_lens with direction (bullish/bearish/ \
   neutral), conviction (low/medium/high), a one-line summary, and 2-4 \
   supporting points grounded in the technicals.
"""


USER_TEMPLATE = """\
Quantitative context for {ticker} as of {as_of}.

Trend / momentum / volatility:
- last_close: {last_close}
- ma_alignment: {ma_alignment}
- sma_50_slope_21d: {sma_50_slope}
- sma_200_slope_63d: {sma_200_slope}
- dist to sma_50 / sma_200: {dist_sma_50} / {dist_sma_200}
- RSI(14): {rsi_14}
- ATR(14): {atr_14} ({atr_14_pct})
- realized vol 21d annualised: {realized_vol_21d}
- volume_ratio_20: {volume_ratio_20}

Returns:
- 5d / 21d / 63d / 252d: {return_5d} / {return_21d} / {return_63d} / {return_252d}

Relative strength:
- vs SPY 21d / 63d: {excess_21d} / {rs_63d}
- pct off 52w high / low: {pct_off_high} / {pct_off_low}
- gap from prev close: {gap}

Levels:
- nearest support / resistance: {support} / {resistance}
- recent_high_63d / base_low: {recent_high} / {base_low}
- pullback from recent high: {pullback}
- breakout distance: {breakout_dist}
- tight range: {tight_range}

Setup classification (deterministic): {setup_type} (confidence: {setup_confidence})

Submit your Quantitative lens via submit_lens.
"""


def run_technical(
    packet: ResearchPacket,
    *,
    client=None,
) -> AgentResult:
    v = packet.view
    user_msg = USER_TEMPLATE.format(
        ticker=packet.ticker,
        as_of=packet.as_of.isoformat(),
        last_close=_fmt(v.market.last_close),
        ma_alignment=v.indicators.ma_alignment,
        sma_50_slope=_fmt_pct(v.indicators.sma_50_slope_21d_pct),
        sma_200_slope=_fmt_pct(v.indicators.sma_200_slope_63d_pct),
        dist_sma_50=_fmt_pct(v.indicators.dist_to_sma_50_pct),
        dist_sma_200=_fmt_pct(v.indicators.dist_to_sma_200_pct),
        rsi_14=_fmt(v.indicators.rsi_14, 0),
        atr_14=_fmt(v.indicators.atr_14),
        atr_14_pct=_fmt_pct(v.indicators.atr_14_pct),
        realized_vol_21d=_fmt_pct(v.indicators.realized_vol_21d_annualized),
        volume_ratio_20=_fmt(v.indicators.volume_ratio_20),
        return_5d=_fmt_pct(v.market.return_5d),
        return_21d=_fmt_pct(v.market.return_21d),
        return_63d=_fmt_pct(v.market.return_63d),
        return_252d=_fmt_pct(v.market.return_252d),
        excess_21d=_fmt_pct(v.market.excess_return_21d),
        rs_63d=_fmt_pct(v.indicators.relative_strength_vs_spy_63d),
        pct_off_high=_fmt_pct(v.market.pct_off_52w_high),
        pct_off_low=_fmt_pct(v.market.pct_off_52w_low),
        gap=_fmt_pct(v.market.gap_from_prev_close),
        support=_fmt(v.levels.nearest_support),
        resistance=_fmt(v.levels.nearest_resistance),
        recent_high=_fmt(v.levels.recent_high_63d),
        base_low=_fmt(v.levels.base_low),
        pullback=_fmt_pct(v.levels.pullback_pct_from_recent_high),
        breakout_dist=_fmt_pct(v.levels.breakout_distance_pct),
        tight_range="yes" if v.levels.is_in_tight_range else "no",
        setup_type=v.setup.setup_type,
        setup_confidence=v.setup.confidence,
    )
    return run_agent(
        agent_name="quantitative",
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


REVISION_SYSTEM_PROMPT = """\
You are the Quantitative analyst on a four-lens swing-trading panel. You \
already submitted a round-1 read. Now you see the OTHER THREE analysts' \
round-1 reads. Your job: revise YOUR read if their evidence changes your \
analysis. Specifically:
- Engage with their reads where relevant — name which lenses you're \
  responding to via `responded_to`.
- It is FINE to leave your direction / conviction unchanged; just \
  acknowledge in `revised_summary` that you considered the other reads.
- DO NOT mimic their disciplines — stay in your lane (technicals, factor \
  exposure, momentum). You can NOTE that fundamental lens is bearish, but \
  don't suddenly start citing PEG ratios.
- `revised_summary`: short (1-2 sentences) headline of your revised read.
- `revised_points`: 1-4 bullets supporting the revised read.
- `responded_to`: lens names ('fundamental', 'sentiment_macro', \
  'contrarian_risk') you specifically engaged with. Empty list = standing \
  pat without addressing any other lens.

Submit via submit_revised_lens.
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


def run_technical_revision(
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
        agent_name="quantitative",
        system_prompt=REVISION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        round_one_lens=round_one_lens,
        client=client,
    )


__all__ = ["run_technical", "run_technical_revision"]
