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

// ---------- Endpoints ----------

export const api = {
  health: () => request<{ status: string }>("/health"),
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
