/**
 * Typed API client for the FastAPI backend.
 *
 * Mirrors apps/api/app/schemas/__init__.py — keep these in sync. We don't
 * codegen for V1 to keep the toolchain simple; type drift is caught by
 * runtime errors during dev.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8001";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, `${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---------- Types (mirrors backend schemas) ----------

export type Creator = {
  id: number;
  display_name: string;
  primary_source_type: string;
  notes: string | null;
  active: boolean;
};

export type LeaderboardRow = {
  creator_id: number;
  creator_name: string;
  n_calls: number;
  n_activated: number;
  n_unique_tickers: number;
  hit_rate: number | null;
  hit_rate_lower_ci: number | null;
  hit_rate_upper_ci: number | null;
  mean_return: number | null;
  mean_excess_return: number | null;
  expectancy_unconditional: number | null;
  sharpe_like: number | null;
};

export type Scorecard = {
  id: number;
  creator_id: number;
  window_label: string;
  horizon: string;
  n_calls: number;
  n_activated: number;
  n_unique_tickers: number;
  activation_rate: number | null;
  hit_rate: number | null;
  hit_rate_lower_ci: number | null;
  hit_rate_upper_ci: number | null;
  mean_return: number | null;
  median_return: number | null;
  std_return: number | null;
  expectancy_unconditional: number | null;
  sharpe_like: number | null;
  mean_mae: number | null;
  worst_mae: number | null;
  mean_excess_return: number | null;
  median_excess_return: number | null;
  excess_hit_rate: number | null;
};

export type Outcome = {
  id: number;
  call_id: number;
  horizon: string;
  activated: boolean;
  activation_at: string | null;
  entry_fill_price: number | null;
  exit_at: string | null;
  exit_price: number | null;
  return_pct: number | null;
  benchmark_return_pct: number | null;
  excess_return_pct: number | null;
  mfe: number | null;
  mae: number | null;
  hit_target: boolean | null;
  hit_stop: boolean | null;
  status: string;
};

export type Call = {
  id: number;
  document_id: number;
  primary_segment_id: number | null;
  ticker: string;
  direction: string;
  entry_type: string;
  entry_price: number | null;
  target_price: number | null;
  stop_price: number | null;
  timeframe: string;
  reasoning_summary: string | null;
  evidence_quote: string | null;
  context_text: string | null;
  context_start_seconds: number | null;
  context_end_seconds: number | null;
  extracted_at: string;
  extractor_version: string;
  llm_confidence: number | null;
  rule_confidence: number | null;
  final_confidence: number;
  status: string;
  validator_notes: string | null;
  manual_status: string;
  manual_notes: string | null;
  manual_reviewed_at: string | null;
};

export type CallWithContext = {
  call: Call;
  creator_id: number | null;
  creator_name: string | null;
  document_title: string | null;
  document_url: string | null;
  posted_at: string;
  outcomes: Outcome[];
};

export type CallsPage = {
  items: CallWithContext[];
  total: number;
  limit: number;
  offset: number;
};

export type DocumentOut = {
  id: number;
  source_type: string;
  external_id: string;
  title: string | null;
  description: string | null;
  posted_at: string;
  url: string | null;
  duration_seconds: number | null;
  creator_id: number | null;
  creator_name: string | null;
};

export type TranscriptSegment = {
  id: number;
  document_id: number;
  start_seconds: number;
  end_seconds: number;
  text: string;
  speaker_label: string | null;
};

export type Annotation = {
  id: number;
  entity_type: string;
  entity_id: string;
  body: string;
  author: string | null;
  created_at: string;
  updated_at: string;
};

export type Tag = {
  id: number;
  entity_type: string;
  entity_id: string;
  label: string;
  created_at: string;
};

export type WatchlistEntry = {
  id: number;
  entity_type: string;
  entity_id: string;
  note: string | null;
  pinned_at: string;
};

export type Bar = {
  time: number;     // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type TickerCall = {
  call_id: number;
  posted_at: string;
  time: number;     // unix seconds
  direction: string;
  entry_type: string;
  entry_price: number | null;
  target_price: number | null;
  stop_price: number | null;
  final_confidence: number;
  status: string;
  creator_id: number;
  creator_name: string;
  doc_title: string | null;
  return_5d: number | null;
  activated: boolean | null;
};

// ADR 0006 — TickerSignal aggregates (per ticker × window × signal_type).
export type TickerSignal = {
  ticker: string;
  window_end: string;
  window_size: "1d" | "7d" | "30d";
  signal_type:
    | "trade_calls"
    | "claims_all"
    | "claims_factual"
    | "creator_consensus";
  n_mentions: number;
  n_distinct_creators: number;
  n_documents: number;
  net_polarity: number | null;
  credibility_weighted_polarity: number | null;
  avg_entry_distance_pct: number | null;
  avg_target_distance_pct: number | null;
  avg_stop_distance_pct: number | null;
  n_factual: number;
  n_opinion: number;
  n_speculation: number;
  n_hype: number;
  computed_at: string;
  aggregator_version: string;
};

// ADR 0006 — Claim (extracted alongside ExtractedCall).
export type TickerClaim = {
  claim_id: number;
  claim_type:
    | "catalyst"
    | "risk"
    | "earnings_view"
    | "macro_theme"
    | "sector_view"
    | "factual_assertion"
    | "opinion"
    | "speculation"
    | "hype";
  ticker: string | null;
  sector: string | null;
  polarity: "bullish" | "bearish" | "neutral" | "mixed";
  claim_class: "factual" | "opinion" | "speculation" | "hype";
  summary: string;
  evidence_quote: string;
  context_start_seconds: number | null;
  context_end_seconds: number | null;
  final_confidence: number;
  status: string;
  extracted_at: string;
  document_id: number;
  document_title: string | null;
  document_url: string | null;
  posted_at: string;
  creator_id: number;
  creator_name: string;
};

// Per-creator coverage rollup for one ticker (IA section 6).
export type TickerCoverage = {
  creator_id: number;
  creator_name: string;
  n_calls: number;
  n_claims: number;
  most_recent_mention_at: string;
  hit_rate_lower_ci: number | null;
  is_calibrated: boolean;
  net_polarity_on_this_ticker: number | null;
};

// ---------------------------------------------------------------------------
// TickerResearchView (mirrors src/app/analysis/schema.py).
// Returned by GET /tickers/{ticker}/research.

export type ResearchIdentity = {
  ticker: string;
  name: string | null;
  asset_type: string | null;
  exchange: string | null;
  sector: string | null;
  in_universe: boolean;
  has_transcript_signals: boolean;
  has_extracted_calls: boolean;
  has_extracted_claims: boolean;
  n_bars_loaded: number;
  enough_history_for_full_analysis: boolean;
  data_freshness_days: number | null;
  missing_data_warnings: string[];
};

export type ResearchValuation = {
  market_cap: number | null;
  forward_pe: number | null;
  trailing_pe: number | null;
  peg_ratio: number | null;
  price_to_sales_ttm: number | null;
  price_to_book: number | null;
  enterprise_to_ebitda: number | null;
  earnings_growth_forward: number | null;
  revenue_growth_yoy: number | null;
  profit_margins: number | null;
  float_shares: number | null;
  shares_outstanding: number | null;
  short_pct_of_float: number | null;
  held_pct_institutions: number | null;
  beta: number | null;
  dividend_yield: number | null;
  days_to_next_earnings: number | null;
  next_earnings_date: string | null;
  sector: string | null;
  industry: string | null;
};

export type ResearchMarketSnapshot = {
  as_of: string;
  last_close: number | null;
  last_bar_at: string | null;
  daily_volume: number | null;
  avg_volume_20d: number | null;
  avg_volume_63d: number | null;
  dollar_volume: number | null;
  high_52w: number | null;
  low_52w: number | null;
  pct_off_52w_high: number | null;
  pct_off_52w_low: number | null;
  return_1d: number | null;
  return_5d: number | null;
  return_21d: number | null;
  return_63d: number | null;
  return_126d: number | null;
  return_252d: number | null;
  gap_from_prev_close: number | null;
  spy_return_5d: number | null;
  spy_return_21d: number | null;
  spy_return_63d: number | null;
  excess_return_5d: number | null;
  excess_return_21d: number | null;
  excess_return_63d: number | null;
};

export type ResearchIndicators = {
  sma_20: number | null;
  sma_50: number | null;
  sma_150: number | null;
  sma_200: number | null;
  ema_8: number | null;
  ema_21: number | null;
  dist_to_sma_20_pct: number | null;
  dist_to_sma_50_pct: number | null;
  dist_to_sma_150_pct: number | null;
  dist_to_sma_200_pct: number | null;
  dist_to_ema_8_pct: number | null;
  dist_to_ema_21_pct: number | null;
  sma_50_slope_21d_pct: number | null;
  sma_200_slope_63d_pct: number | null;
  ma_alignment: "bullish_stack" | "bearish_stack" | "mixed" | "insufficient";
  rsi_14: number | null;
  atr_14: number | null;
  atr_14_pct: number | null;
  realized_vol_21d_annualized: number | null;
  volume_ratio_20: number | null;
  relative_strength_vs_spy_63d: number | null;
  relative_strength_vs_spy_126d: number | null;
};

export type ResearchLevels = {
  swing_highs: number[];
  swing_lows: number[];
  nearest_resistance: number | null;
  nearest_support: number | null;
  nearest_resistance_distance_pct: number | null;
  nearest_support_distance_pct: number | null;
  recent_high_63d: number | null;
  recent_low_63d: number | null;
  pullback_pct_from_recent_high: number | null;
  consolidation_range_pct: number | null;
  is_in_tight_range: boolean;
  breakout_distance_pct: number | null;
  base_low: number | null;
  base_high: number | null;
};

export type SetupType =
  | "strong_uptrend"
  | "uptrend_pullback"
  | "breakout_candidate"
  | "extended_momentum"
  | "range_bound"
  | "downtrend"
  | "high_volatility_unstable"
  | "low_liquidity"
  | "insufficient_data"
  | "unclear";

export type ResearchSetup = {
  setup_type: SetupType;
  confidence: "low" | "medium" | "high";
  reasons: string[];
  counterarguments: string[];
  supporting_metrics: Record<string, number | string | null>;
  data_sufficiency: "sufficient" | "partial" | "insufficient";
};

export type StyleType =
  | "momentum_breakout"
  | "trend_pullback"
  | "mean_reversion"
  | "base_breakout"
  | "relative_strength_leader"
  | "not_suitable_now";

export type ResearchStyleFitItem = {
  style: StyleType;
  fit_level: "high" | "medium" | "low";
  reasons: string[];
  relevant_levels: Record<string, number | null>;
  invalidation_conditions: string[];
  what_would_improve: string[];
  what_would_weaken: string[];
};

export type ResearchStyleFit = {
  items: ResearchStyleFitItem[];
  primary_style: StyleType | null;
};

export type DecisionStatus =
  | "research_candidate"
  | "watch"
  | "wait_for_setup"
  | "skip_for_now"
  | "extended_risk"
  | "insufficient_data";

export type ResearchRubricEntry = {
  name: string;
  value: number | string | null;
  threshold: number | string | null;
  passed: boolean | null;
  weight: "low" | "medium" | "high";
  note: string | null;
};

export type ResearchStatus = {
  status: DecisionStatus;
  confidence: "low" | "medium" | "high";
  summary: string;
  rubric: ResearchRubricEntry[];
  caveats: string[];
};

export type ActionLabel =
  | "BUY"
  | "ACCUMULATE"
  | "HOLD"
  | "WAIT"
  | "REDUCE"
  | "AVOID"
  | "N/A";

export type ResearchAction = {
  label: ActionLabel;
  confidence: "low" | "medium" | "high";
  derivation: string;
  rubric_pass_rate: number | null;
  notes: string[];
};

export type ResearchEntryZone = {
  available: boolean;
  reason_unavailable: string | null;
  setup_trigger_level: number | null;
  candidate_research_zone_low: number | null;
  candidate_research_zone_high: number | null;
  invalidation_reference: number | null;
  risk_reference_pct: number | null;
  risk_reference_atrs: number | null;
  nearest_resistance: number | null;
  risk_reward_estimate: number | null;
  method_notes: string[];
  language_disclaimer: string;
};

export type ResearchTranscript = {
  has_data: boolean;
  n_signals: number;
  n_calls: number;
  n_claims: number;
  most_recent_mention_at: string | null;
  days_since_most_recent: number | null;
  coverage_status: "fresh" | "stale" | "historical_only" | "absent";
  n_distinct_creators: number;
  n_calibrated_creators: number;
  net_polarity_30d: number | null;
  credibility_weighted_polarity_30d: number | null;
  sample_size_caveat: string | null;
  summary: string;
  confirms_or_contradicts: "confirms" | "contradicts" | "irrelevant" | "unknown";
  notes: string[];
};

export type ScanRow = {
  ticker: string;
  status: DecisionStatus;
  status_confidence: "low" | "medium" | "high";
  setup_type: SetupType;
  setup_confidence: "low" | "medium" | "high";
  primary_style: StyleType | null;
  summary: string;
  last_close: number | null;
  pct_off_52w_high: number | null;
  return_5d: number | null;
  return_21d: number | null;
  rsi_14: number | null;
  atr_14_pct: number | null;
  relative_strength_vs_spy_63d: number | null;
  pullback_pct_from_recent_high: number | null;
  breakout_distance_pct: number | null;
  entry_zone_available: boolean;
  entry_trigger: number | null;
  invalidation_reference: number | null;
  risk_reward_estimate: number | null;
  transcript_n_calls: number;
  transcript_n_claims: number;
  transcript_polarity_30d: number | null;
  transcript_confirms: "confirms" | "contradicts" | "irrelevant" | "unknown";
  n_bars_loaded: number;
  in_universe: boolean;
  error: string | null;
};

export type ScanResult = {
  as_of: string;
  tickers_requested: number;
  rows: ScanRow[];
  n_research_candidates: number;
  n_watch: number;
  n_skip: number;
  n_errors: number;
};

export type ResearchSnapshotRow = {
  ticker: string;
  as_of: string;
  status: DecisionStatus;
  status_confidence: "low" | "medium" | "high";
  setup_type: SetupType;
  setup_confidence: "low" | "medium" | "high";
  primary_style: StyleType | null;
  last_close: number | null;
  pct_off_52w_high: number | null;
  rsi_14: number | null;
  atr_14_pct: number | null;
  ma_alignment: string;
  sma_50_slope_21d_pct: number | null;
  relative_strength_vs_spy_63d: number | null;
  pullback_pct_from_recent_high: number | null;
  breakout_distance_pct: number | null;
  entry_zone_available: boolean;
  entry_trigger: number | null;
  transcript_n_calls: number;
  transcript_n_claims: number;
  transcript_polarity_30d: number | null;
  transcript_confirms: string;
  computed_at: string;
};

export type TickerResearchView = {
  ticker: string;
  as_of: string;
  identity: ResearchIdentity;
  market: ResearchMarketSnapshot;
  indicators: ResearchIndicators;
  levels: ResearchLevels;
  valuation: ResearchValuation;
  setup: ResearchSetup;
  style_fit: ResearchStyleFit;
  status: ResearchStatus;
  action: ResearchAction;
  entry_zone: ResearchEntryZone;
  transcript: ResearchTranscript;
  methodology_links: string[];
  disclaimer: string;
};

// ---------------------------------------------------------------------------
// Entry/Exit research feature (docs/entry-exit-research-plan.md).
// Returned by POST /research/quick/{ticker} (and POST /research/deep in Phase 2).

export type ResearchZoneBand = {
  low: number;
  high: number;
  method: string;
  rationale: string;
};

export type ResearchAgentNote = {
  agent: string;
  confidence: "low" | "medium" | "high";
  bull_points: string[];
  bear_points: string[];
  note: string;
};

export type LensName =
  | "quantitative"
  | "fundamental"
  | "sentiment_macro"
  | "contrarian_risk";

export type LensView = {
  name: LensName;
  direction: "bullish" | "bearish" | "neutral";
  conviction: "low" | "medium" | "high";
  summary: string;
  points: string[];
  revised_summary?: string | null;
  revised_points?: string[];
  responded_to?: string[];
};

export type RRCombo = {
  entry_kind: "breakout" | "pullback";
  primary_index: number;
  runner_index: number | null;
  invalidation_index: number;
  entry_label: string;
  primary_label: string;
  runner_label: string | null;
  invalidation_label: string;
  rr_primary: number;
  rr_runner: number | null;
  rr_blended: number;
  is_chosen: boolean;
};

export type RRDistribution = {
  min_rr: number;
  median_rr: number;
  max_rr: number;
  n_combos: number;
  combos: RRCombo[];
};

export type EntryExitPlan = {
  ticker: string;
  as_of: string;
  entry_zone: ResearchZoneBand;
  pullback_entry_zone: ResearchZoneBand | null;
  exit_zone_primary: ResearchZoneBand;
  exit_zone_runner: ResearchZoneBand | null;
  invalidation: number;
  risk_reward_primary: number;
  risk_reward_runner: number | null;
  plan_r_r_blended: number | null;
  r_r_distribution: RRDistribution | null;
  confidence: "low" | "medium" | "high";
  timeframe: "1-3d" | "5-15d" | "2-6w";
  bull_case: string[];
  bear_case: string[];
  key_risks: string[];
  lenses: LensView[];
  mode: "quick" | "deep";
  cost_usd: number;
  duration_ms: number;
  sources_used: string[];
  agent_trace: ResearchAgentNote[];
  disclaimer: string;
};

export type GoldLabel = {
  id: number;
  source_key: string;
  source_text: string;
  origin_call_id: number | null;
  origin_document_id: number | null;
  expected_is_call: boolean;
  expected_ticker: string | null;
  expected_direction: string | null;
  expected_entry_type: string | null;
  expected_entry_price: number | null;
  expected_target_price: number | null;
  expected_stop_price: number | null;
  expected_timeframe: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

// ---------------------------------------------------------------------------
// Portfolio (Task 5's endpoints — manual trade ledger + live positions).

export type PortfolioTrade = {
  id: number;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price_per_share: number;
  currency: string;
  fees: number;
  traded_at: string;
  eur_amount: number | null;
  note: string | null;
};

export type PositionView = {
  ticker: string;
  currency: string;
  quantity: number;
  avg_cost: number | null;
  cost_basis: number;
  realized_pnl: number;
  eur_avg_cost: number | null;
  eur_cost_basis: number | null;
  eur_realized_pnl: number | null;
  first_traded_at: string;
  last_traded_at: string;
  trade_count: number;
  last_price: number | null;
  previous_close: number | null;
  market_value: number | null;
  market_value_eur: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  day_change_pct: number | null;
};

export type PortfolioView = {
  open_positions: PositionView[];
  closed_positions: PositionView[];
  total_market_value: number | null;
  total_market_value_eur: number | null;
  total_unrealized_pnl: number | null;
  total_realized_pnl: number | null;
  eur_usd_rate: number | null;
  quote_errors: string[];
  as_of: string;
};

export type TradeInput = {
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price_per_share: number;
  currency?: string;
  fees?: number;
  traded_at: string;
  eur_amount?: number | null;
  note?: string | null;
};

// ---------- Endpoints ----------

export const api = {
  health: () => request<{ status: string }>("/health"),
  portfolio: {
    get: (withQuotes = true) =>
      request<PortfolioView>(`/portfolio?with_quotes=${withQuotes}`),
    trades: (ticker?: string) =>
      request<PortfolioTrade[]>(
        `/portfolio/trades${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`,
      ),
    addTrade: (body: TradeInput) =>
      request<PortfolioTrade>("/portfolio/trades", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    updateTrade: (id: number, body: Partial<TradeInput>) =>
      request<PortfolioTrade>(`/portfolio/trades/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    deleteTrade: (id: number) =>
      request<void>(`/portfolio/trades/${id}`, { method: "DELETE" }),
  },
  creators: {
    list: (activeOnly = false) =>
      request<Creator[]>(`/creators${activeOnly ? "?active_only=true" : ""}`),
    get: (id: number) => request<Creator>(`/creators/${id}`),
  },
  leaderboard: {
    get: (horizon = "5d", windowLabel = "all") =>
      request<LeaderboardRow[]>(
        `/leaderboard?horizon=${horizon}&window_label=${windowLabel}`,
      ),
    scorecards: (creatorId: number) =>
      request<Scorecard[]>(`/leaderboard/scorecards/${creatorId}`),
  },
  calls: {
    list: (params: {
      creator_id?: number;
      ticker?: string;
      direction?: string;
      status?: string;
      manual_status?: string;
      min_confidence?: number;
      limit?: number;
      offset?: number;
      order?: "recent" | "confidence" | "return";
    } = {}) => {
      const q = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") q.append(k, String(v));
      });
      return request<CallsPage>(`/calls?${q.toString()}`);
    },
    get: (id: number) => request<CallWithContext>(`/calls/${id}`),
  },
  documents: {
    get: (id: number) => request<DocumentOut>(`/documents/${id}`),
    segments: (id: number) =>
      request<TranscriptSegment[]>(`/documents/${id}/segments`),
  },
  tickers: {
    list: () => request<Array<{ ticker: string; n_calls: number; last_seen: string }>>("/tickers"),
    get: (ticker: string) => request<{
      ticker: string; n_calls: number;
      by_direction: Record<string, number>;
      by_outcome: Record<string, number>;
    }>(`/tickers/${ticker}`),
    bars: (ticker: string, days = 180) =>
      request<{ ticker: string; bars: Bar[] }>(`/tickers/${ticker}/bars?days=${days}`),
    calls: (ticker: string) => request<TickerCall[]>(`/tickers/${ticker}/calls`),
    signals: (
      ticker: string,
      params: { signal_type?: string; window_size?: string } = {},
    ) => {
      const q = new URLSearchParams();
      if (params.signal_type) q.append("signal_type", params.signal_type);
      if (params.window_size) q.append("window_size", params.window_size);
      const qs = q.toString();
      return request<TickerSignal[]>(
        `/tickers/${ticker}/signals${qs ? `?${qs}` : ""}`,
      );
    },
    claims: (
      ticker: string,
      params: {
        since?: string;
        claim_type?: string[];
        claim_class?: string[];
        polarity?: string;
        creator_id?: number;
        status?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => {
      const q = new URLSearchParams();
      if (params.since) q.append("since", params.since);
      if (params.polarity) q.append("polarity", params.polarity);
      if (params.creator_id !== undefined) q.append("creator_id", String(params.creator_id));
      if (params.status) q.append("status", params.status);
      if (params.limit !== undefined) q.append("limit", String(params.limit));
      if (params.offset !== undefined) q.append("offset", String(params.offset));
      (params.claim_type || []).forEach((t) => q.append("claim_type", t));
      (params.claim_class || []).forEach((c) => q.append("claim_class", c));
      const qs = q.toString();
      return request<TickerClaim[]>(
        `/tickers/${ticker}/claims${qs ? `?${qs}` : ""}`,
      );
    },
    coverage: (ticker: string) =>
      request<TickerCoverage[]>(`/tickers/${ticker}/coverage`),
    research: (
      ticker: string,
      params: { history_days?: number; benchmark?: string; fetch_metadata?: boolean } = {},
    ) => {
      const q = new URLSearchParams();
      if (params.history_days !== undefined) q.append("history_days", String(params.history_days));
      if (params.benchmark) q.append("benchmark", params.benchmark);
      if (params.fetch_metadata !== undefined)
        q.append("fetch_metadata", String(params.fetch_metadata));
      const qs = q.toString();
      return request<TickerResearchView>(
        `/tickers/${ticker}/research${qs ? `?${qs}` : ""}`,
      );
    },
  },
  research: {
    scan: (params: {
      tickers?: string[];
      source?: "watchlist";
      persist?: boolean;
      fetch_metadata?: boolean;
    } = {}) => {
      const q = new URLSearchParams();
      if (params.tickers && params.tickers.length > 0) q.append("tickers", params.tickers.join(","));
      if (params.source) q.append("source", params.source);
      if (params.persist !== undefined) q.append("persist", String(params.persist));
      if (params.fetch_metadata !== undefined) q.append("fetch_metadata", String(params.fetch_metadata));
      return request<ScanResult>(`/research/scan?${q.toString()}`);
    },
    snapshots: (ticker: string, params: { days?: number; limit?: number } = {}) => {
      const q = new URLSearchParams();
      if (params.days !== undefined) q.append("days", String(params.days));
      if (params.limit !== undefined) q.append("limit", String(params.limit));
      const qs = q.toString();
      return request<ResearchSnapshotRow[]>(
        `/research/snapshots/${ticker}${qs ? `?${qs}` : ""}`,
      );
    },
    quickGet: async (ticker: string) => {
      try {
        return await request<EntryExitPlan | null>(`/research/quick/${ticker}`);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    quickRun: (ticker: string, force = false) =>
      request<EntryExitPlan>(
        `/research/quick/${ticker}${force ? "?force=true" : ""}`,
        { method: "POST" },
      ),
    deepGet: async (ticker: string) => {
      try {
        return await request<EntryExitPlan | null>(`/research/deep/${ticker}`);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    deepRun: (ticker: string, force = false) =>
      request<EntryExitPlan>(
        `/research/deep/${ticker}${force ? "?force=true" : ""}`,
        { method: "POST" },
      ),
  },
  annotations: {
    list: (entity_type: string, entity_id: string) =>
      request<Annotation[]>(
        `/annotations?entity_type=${entity_type}&entity_id=${entity_id}`,
      ),
    create: (data: { entity_type: string; entity_id: string; body: string; author?: string }) =>
      request<Annotation>("/annotations", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, body: string) =>
      request<Annotation>(`/annotations/${id}`, { method: "PATCH", body: JSON.stringify({ body }) }),
    delete: (id: number) =>
      request<void>(`/annotations/${id}`, { method: "DELETE" }),
  },
  tags: {
    list: (entity_type: string, entity_id: string) =>
      request<Tag[]>(`/tags?entity_type=${entity_type}&entity_id=${entity_id}`),
    labels: () => request<string[]>("/tags/labels"),
    create: (data: { entity_type: string; entity_id: string; label: string }) =>
      request<Tag>("/tags", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<void>(`/tags/${id}`, { method: "DELETE" }),
  },
  watchlist: {
    list: (entity_type?: string) =>
      request<WatchlistEntry[]>(
        `/watchlist${entity_type ? `?entity_type=${entity_type}` : ""}`,
      ),
    pin: (data: { entity_type: string; entity_id: string; note?: string }) =>
      request<WatchlistEntry>("/watchlist", { method: "POST", body: JSON.stringify(data) }),
    unpin: (id: number) =>
      request<void>(`/watchlist/${id}`, { method: "DELETE" }),
  },
  review: {
    setStatus: (callId: number, manual_status: string, manual_notes?: string) =>
      request<Call>(`/review/calls/${callId}`, {
        method: "POST",
        body: JSON.stringify({ manual_status, manual_notes }),
      }),
  },
  gold: {
    list: () => request<GoldLabel[]>("/gold"),
    get: (id: number) => request<GoldLabel>(`/gold/${id}`),
    create: (data: Partial<GoldLabel> & { source_key: string; source_text: string; expected_is_call: boolean }) =>
      request<GoldLabel>("/gold", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<GoldLabel>) =>
      request<GoldLabel>(`/gold/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<void>(`/gold/${id}`, { method: "DELETE" }),
    importJsonl: () => request<{ inserted: number; updated: number }>("/gold/import", { method: "POST" }),
  },
};
