"""The judge receives valuation context and is told to reconcile it."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.analysis.digest import build_panel_digest
from app.analysis.schema import (
    ActionSignal,
    DecisionSupportStatus,
    EntryZoneCandidate,
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
    StyleFitPanel,
    TickerResearchView,
    TranscriptContext,
    ValuationPanel,
)
from app.research.agents import judge as judge_mod

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _view():
    # TickerResearchView requires identity/setup/style_fit/status/action/
    # entry_zone/transcript with no defaults (see test_panel_digest.py's
    # `_complete_view` docstring) — the brief's stripped-down fixture omits
    # them, so this fills them with minimal-but-valid stand-ins and keeps
    # only the market/valuation/indicators numbers the brief specified.
    return TickerResearchView(
        ticker="NVDA",
        as_of=AS_OF,
        identity=IdentityCoverage(
            ticker="NVDA",
            in_universe=True,
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=False,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
        market=MarketSnapshotPanel(as_of=AS_OF, last_close=100.0),
        valuation=ValuationPanel(forward_pe=41.0, revenue_growth_yoy=0.25, fetched_at=AS_OF),
        indicators=IndicatorPanel(rsi_14=71.0, atr_14=3.5),
        levels=LevelsPanel(),
        setup=SetupClassification(setup_type="breakout_candidate", confidence="medium"),
        style_fit=StyleFitPanel(),
        status=DecisionSupportStatus(status="watch", confidence="medium", summary="test fixture"),
        action=ActionSignal(label="HOLD", confidence="medium", derivation="test fixture"),
        entry_zone=EntryZoneCandidate(available=False),
        transcript=TranscriptContext(has_data=False, n_signals=0, n_calls=0, n_claims=0),
    )


def test_user_template_has_a_valuation_slot():
    assert "{valuation_block}" in judge_mod.USER_TEMPLATE


def test_system_prompt_instructs_reconciliation():
    prompt = judge_mod.SYSTEM_PROMPT.lower()
    assert "valuation" in prompt


def test_formatted_message_contains_the_valuation_numbers():
    digest = build_panel_digest(_view())
    block = digest.valuation_block()
    assert "forward_pe" in block
    assert "41" in block


def test_missing_digest_renders_an_explicit_placeholder():
    """run_judge must stay callable without a digest — existing callers pass none."""
    block = judge_mod._valuation_block_for(None)
    assert "not available" in block.lower() or "no valuation" in block.lower()


def test_digest_block_is_used_when_present():
    digest = build_panel_digest(_view())
    block = judge_mod._valuation_block_for(digest)
    assert "forward_pe" in block


def test_valuation_block_names_unavailable_fields():
    """Absence must be visible to the judge, not inferred from silence."""
    digest = build_panel_digest(_view())
    block = judge_mod._valuation_block_for(digest)
    assert "unavailable" in block.lower()
    assert "trailing_pe" in block  # present in `missing`, absent from measures


def _stub_peers():
    # Attribute names must match `_PEER_FIELDS` / `_PEER_PROPERTIES` in
    # app/analysis/digest.py exactly — `build_panel_digest` reads them via
    # getattr(source, name, None).
    return SimpleNamespace(
        median_forward_pe=18.0,
        median_peg=1.4,
        median_revenue_growth_yoy=0.11,
        median_profit_margins=0.14,
        forward_pe_relative="above_peers",
        growth_relative="above_peers",
        margin_relative="in_line",
    )


def test_valuation_block_includes_peer_medians_when_available():
    """The judge is told to cite 'vs peers.median_forward_pe' — it must be
    able to see that field, not just the ticker's own valuation."""
    digest = build_panel_digest(_view(), peers=_stub_peers())
    block = judge_mod._valuation_block_for(digest)
    assert "median_forward_pe" in block
    assert "18" in block


def test_valuation_block_has_no_peer_heading_when_peers_absent():
    """No peer set → no 'Peer group' section, but valuation still renders."""
    digest = build_panel_digest(_view())
    block = judge_mod._valuation_block_for(digest)
    assert "Peer group" not in block
    assert "forward_pe" in block
