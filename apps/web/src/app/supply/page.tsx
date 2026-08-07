"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { MarginHistoryChart } from "@/components/MarginHistoryChart";
import {
  api,
  ApiError,
  type SupplyConstraint,
  type SupplyEndMarketConcentration,
  type SupplyScreenRow,
} from "@/lib/api";
import { cn, fmtDateTime } from "@/lib/utils";

/**
 * Supply-constraint screener page — makes `tsr supply-screen` (Layer A) and
 * the hand-curated constraint registry (Layer B) visible and re-runnable
 * from the dashboard instead of CLI-only.
 *
 * Three sections: ranked watchlist (score components inline, never behind
 * a click), constraint registry (staleness surfaced), candidate detail
 * (gross-margin history — the one chart that matters here). No price data
 * anywhere: this is a fundamentals screen and price action was explicitly
 * called out as a distraction on it.
 */
export default function SupplyScreenerPage() {
  const qc = useQueryClient();
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);

  const {
    data: screen,
    isLoading: screenLoading,
    error: screenError,
  } = useQuery({
    queryKey: ["supply-screen"],
    queryFn: () => api.supply.screen(50),
    staleTime: 60_000,
  });

  const { data: constraints, error: constraintsError } = useQuery({
    queryKey: ["supply-constraints"],
    queryFn: () => api.supply.constraints(),
  });

  const refresh = useMutation({
    mutationFn: () => api.supply.refresh(50),
    onSuccess: (data) => {
      qc.setQueryData(["supply-screen"], data);
      setArmed(false);
    },
    onError: () => setArmed(false),
  });

  function onRowClick(ticker: string) {
    setExpandedTicker((prev) => (prev === ticker ? null : ticker));
    setSelectedTicker(ticker);
  }

  return (
    <div className="space-y-6 font-mono-jb">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Supply-constraint screener</h1>
        <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1.5 max-w-3xl normal-case">
          Gross-margin-compression candidates (Layer A) joined to a hand-curated
          supply-constraint registry (Layer B). Decision support only — nothing
          here is a price target, an entry price, or a recommendation.
        </p>
      </div>

      <ReRunBar
        asOf={screen?.as_of ?? null}
        coverage={screen?.coverage ?? null}
        armed={armed}
        pending={refresh.isPending}
        onArm={() => setArmed(true)}
        onCancel={() => setArmed(false)}
        onConfirm={() => refresh.mutate()}
        error={refresh.error instanceof ApiError ? refresh.error.message : null}
      />

      {screenError && (
        <ErrorBanner error={screenError} fallback="Could not load the supply screen." />
      )}

      {screen?.concentration && <ConcentrationBanner concentration={screen.concentration} />}

      <RankedWatchlist
        rows={screen?.rows ?? []}
        loading={screenLoading}
        expandedTicker={expandedTicker}
        onRowClick={onRowClick}
      />

      {constraintsError ? (
        <ErrorBanner error={constraintsError} fallback="Could not load the constraint registry." />
      ) : (
        <ConstraintRegistry constraints={constraints ?? []} />
      )}

      <CandidateDetail ticker={selectedTicker} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Re-run research bar

function ReRunBar({
  asOf,
  coverage,
  armed,
  pending,
  onArm,
  onCancel,
  onConfirm,
  error,
}: {
  asOf: string | null;
  coverage: { universe: number; resolved: number; sufficient_history: number; passed: number } | null;
  armed: boolean;
  pending: boolean;
  onArm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
  error: string | null;
}) {
  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] px-4 py-3 flex items-center justify-between gap-4 flex-wrap">
      <div className="text-[11px] text-[var(--muted-foreground)] space-y-0.5">
        <div>
          as_of <span className="text-[var(--foreground)]">{asOf ? fmtDateTime(asOf) : "—"}</span>
        </div>
        <div className="uppercase tracking-wider text-[10px]">
          {coverage
            ? `universe=${coverage.universe} · resolved=${coverage.resolved} · sufficient_history=${coverage.sufficient_history} · passed=${coverage.passed}`
            : "coverage unavailable"}
        </div>
      </div>

      <div className="flex items-center gap-3">
        {error && <span className="text-[11px] text-[var(--negative)]">{error}</span>}
        {!armed ? (
          <button
            onClick={onArm}
            disabled={pending}
            className="text-[11px] uppercase tracking-wider px-3 py-1.5 border border-[var(--border)] hover:bg-[var(--muted)] disabled:opacity-50"
          >
            {pending ? "Running…" : "Re-run research"}
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-[var(--warning)] max-w-[28ch]">
              This purges the EDGAR cache and takes several minutes — it hits SEC
              once per resolved ticker.
            </span>
            <button
              onClick={onConfirm}
              disabled={pending}
              className="text-[11px] uppercase tracking-wider px-3 py-1.5 border border-[var(--warning)] text-[var(--warning)] hover:bg-[var(--warning)]/10 disabled:opacity-50"
            >
              {pending ? "Running…" : "Confirm re-run"}
            </button>
            <button
              onClick={onCancel}
              disabled={pending}
              className="text-[11px] uppercase tracking-wider px-2 py-1.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// End-market concentration banner — REPORTING ONLY. Never reorders or
// weights the ranked watchlist below; see apps/api's supply router /
// app.supply.concentration for why end_market is deliberately coarser than
// sector (TiO2 pigment and PVC pipe are different sectors, same end market).

function ConcentrationBanner({ concentration: c }: { concentration: SupplyEndMarketConcentration }) {
  const isConcentrated = c.dominant_end_market !== null && c.dominant_count >= 2;
  return (
    <section
      className={cn(
        "border px-4 py-3 flex items-center justify-between gap-4 flex-wrap",
        isConcentrated
          ? "border-[var(--warning)]/40 bg-[var(--warning)]/5"
          : "border-[var(--border)] bg-[var(--panel)]",
      )}
    >
      <div className="text-[11px] normal-case">
        <span
          className={cn(
            "uppercase tracking-wider text-[10px] mr-2",
            isConcentrated ? "text-[var(--warning)]" : "text-[var(--muted-2)]",
          )}
        >
          end-market concentration
        </span>
        <span className={isConcentrated ? "text-[var(--foreground)] font-medium" : "text-[var(--muted-foreground)]"}>
          {c.summary}
        </span>
        <span className="text-[var(--muted-2)] ml-2">
          (classified {c.rows_classified}/{c.rows_considered} — reporting only, never reorders the table)
        </span>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section 1: ranked watchlist

function RankedWatchlist({
  rows,
  loading,
  expandedTicker,
  onRowClick,
}: {
  rows: SupplyScreenRow[];
  loading: boolean;
  expandedTicker: string | null;
  onRowClick: (ticker: string) => void;
}) {
  return (
    <section>
      <header className="mb-3">
        <h2 className="text-sm tracking-tight">Ranked watchlist</h2>
        <p className="text-[11px] text-[var(--muted-foreground)] mt-1 normal-case">
          Ranked by earnings_torque descending. Every score component is a
          visible column — nothing here is a hidden number. Click a row for its
          near-miss detail and to load its gross-margin chart below.
        </p>
      </header>

      {loading && (
        <div className="text-xs text-[var(--muted-foreground)] px-4 py-3 border border-[var(--border)] bg-[var(--panel)]">
          Running the screen… fast with a warm EDGAR cache, minutes on a cold one.
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div className="text-xs text-[var(--muted-foreground)] px-4 py-3 border border-[var(--border)] bg-[var(--panel)]">
          No candidates returned.
        </div>
      )}

      {!loading && rows.length > 0 && (
        <div className="overflow-x-auto bg-[var(--panel)] border border-[var(--border)]">
          {/* w-max, not w-full: with nowrap cells a full-width table is forced to the
            container and the right-hand columns (status, trigger) clip instead of
            scrolling. Sizing to content lets the overflow-x-auto wrapper do its job. */}
        <table className="min-w-full w-max font-mono-jb text-[11px]">
            <thead>
              <tr className="text-left uppercase tracking-wider text-[var(--muted-foreground)] border-b border-[var(--hairline-2)]">
                <Th>#</Th>
                <Th>Ticker</Th>
                <Th>Sector</Th>
                <Th>End market</Th>
                <Th right>Torque</Th>
                <Th right>GM %ile</Th>
                <Th right>Headroom pp</Th>
                <Th right>Cap intensity</Th>
                <Th right>Survival (q)</Th>
                <Th right>History (q)</Th>
                <Th right>Analysts</Th>
                <Th right>Coverage×</Th>
                <Th>Status</Th>
                <Th>Trigger</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <RowGroup
                  key={row.ticker}
                  rank={i + 1}
                  row={row}
                  expanded={expandedTicker === row.ticker}
                  onClick={() => onRowClick(row.ticker)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RowGroup({
  rank,
  row,
  expanded,
  onClick,
}: {
  rank: number;
  row: SupplyScreenRow;
  expanded: boolean;
  onClick: () => void;
}) {
  const m = row.metrics;
  return (
    <>
      <tr
        onClick={onClick}
        className={cn(
          "border-b border-[var(--hairline-2)] cursor-pointer hover:bg-[color:rgba(255,255,255,0.03)]",
          row.passed ? "border-l-2 border-l-[var(--positive)]" : "border-l-2 border-l-transparent",
          expanded && "bg-[color:rgba(255,255,255,0.03)]",
        )}
      >
        <Td className="text-[var(--muted-2)]">{rank}</Td>
        <Td className="font-medium">{row.ticker}</Td>
        <Td className="text-[var(--muted-foreground)]">{row.sector ?? "—"}</Td>
        <Td className="text-[var(--muted-foreground)] normal-case">{row.end_market ?? "—"}</Td>
        <Td right className={row.passed ? "text-[var(--positive)]" : ""}>
          {num(m?.earnings_torque, 2)}
        </Td>
        <Td right>{pct(m?.gm_percentile)}</Td>
        <Td right>{num(m?.margin_headroom_pp, 1)}</Td>
        <Td right>{num(m?.capital_intensity, 2)}</Td>
        <Td right>{m?.survivability_quarters === null || m?.survivability_quarters === undefined ? (m ? "∞" : "—") : num(m.survivability_quarters, 1)}</Td>
        <Td right className={m && !m.sufficient_history ? "text-[var(--warning)]" : ""}>
          {m ? m.quarters_of_history : "—"}
        </Td>
        <Td right>{m?.analyst_count ?? "—"}</Td>
        <Td right>{num(m?.coverage_multiplier, 2)}</Td>
        <Td>
          <StatusPill row={row} />
        </Td>
        <Td>
          <TriggerPill trigger={row.trigger} />
        </Td>
      </tr>
      {expanded && (
        <tr className="border-b border-[var(--hairline-2)] bg-[color:rgba(255,255,255,0.015)]">
          <td colSpan={14} className="px-2.5 py-3">
            <RowDetail row={row} />
          </td>
        </tr>
      )}
    </>
  );
}

function RowDetail({ row }: { row: SupplyScreenRow }) {
  const m = row.metrics;
  return (
    <div className="space-y-2 text-[11px] normal-case">
      {!row.passed && row.reasons.length > 0 && (
        <div>
          <span className="uppercase tracking-wider text-[10px] text-[var(--warning)]">
            Near-miss — failing criteria:
          </span>
          <ul className="mt-1 list-disc list-inside text-[var(--muted-foreground)] space-y-0.5">
            {row.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[var(--muted-foreground)]">
        <span>
          gm_volatility_pp: <span className="text-[var(--foreground)]">{num(m?.gm_volatility_pp, 1)}</span>
        </span>
        <span>
          dropped_implausible quarters:{" "}
          <span className={row.dropped_implausible > 0 ? "text-[var(--warning)]" : "text-[var(--foreground)]"}>
            {row.dropped_implausible}
          </span>
        </span>
        {row.cik !== null && (
          <span>
            CIK: <span className="text-[var(--foreground)]">{row.cik}</span>
          </span>
        )}
      </div>
      {m?.caveats && m.caveats.length > 0 && (
        <ul className="text-[10px] text-[var(--muted-2)] space-y-0.5">
          {m.caveats.map((c) => (
            <li key={c}>· {c}</li>
          ))}
        </ul>
      )}
      {row.trigger && (
        <div>
          <span className="uppercase tracking-wider text-[10px] text-[var(--muted-2)] mr-1">
            Layer C — gross-margin inflection:
          </span>
          <span
            className={cn(
              row.trigger.draws_attention ? "text-[var(--positive)]" : "text-[var(--muted-foreground)]",
            )}
          >
            [{row.trigger.status}] {row.trigger.detail}
          </span>
        </div>
      )}
      <p className="text-[10px] text-[var(--info)]">
        Selected below — see candidate detail for gross-margin history.
      </p>
    </div>
  );
}

function StatusPill({ row }: { row: SupplyScreenRow }) {
  if (row.metrics && !row.metrics.sufficient_history) {
    return (
      <span className="uppercase tracking-wider text-[var(--warning)]">insufficient history</span>
    );
  }
  if (row.passed) {
    return <span className="uppercase tracking-wider text-[var(--positive)]">passed</span>;
  }
  if (row.metrics) {
    return <span className="uppercase tracking-wider text-[var(--muted-foreground)]">near-miss</span>;
  }
  return <span className="uppercase tracking-wider text-[var(--muted-2)]">unresolved</span>;
}

function TriggerPill({ trigger }: { trigger: SupplyScreenRow["trigger"] }) {
  // Only firing/confirmed draw attention — armed and not_armed stay quiet,
  // per the brief (mirrors `InflectionTrigger.draws_attention`).
  if (!trigger || !trigger.draws_attention) {
    return <span className="text-[var(--muted-2)]">—</span>;
  }
  return (
    <span
      className={cn(
        "uppercase tracking-wider px-1.5 py-0.5 border",
        trigger.status === "confirmed"
          ? "text-[var(--positive)] border-[var(--positive)]"
          : "text-[var(--info)] border-[var(--info)]",
      )}
      title={trigger.detail}
    >
      {trigger.status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Section 2: constraint registry

function ConstraintRegistry({ constraints }: { constraints: SupplyConstraint[] }) {
  return (
    <section>
      <header className="px-0 mb-3 flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm tracking-tight">Constraint registry</h2>
          <p className="text-[11px] text-[var(--muted-foreground)] mt-1 normal-case max-w-3xl">
            Hand-curated, one entry per named supply-constraint thesis. Kept
            deliberately sparse: adding an entry requires an independently
            checkable, dated source (no source, no constraint) — and, as the
            TiO2 entry below shows, a low-confidence thesis with real
            counter-evidence is recorded as such rather than left out or
            inflated.
          </p>
        </div>
        <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-2)] whitespace-nowrap">
          yours · edit configs/supply_constraints.yaml
        </span>
      </header>

      {constraints.length === 0 ? (
        <div className="text-xs text-[var(--muted-foreground)] px-4 py-3 border border-[var(--border)] bg-[var(--panel)] normal-case">
          No constraints registered yet — the registry starts empty until a
          dated, sourced deficit thesis is added to
          configs/supply_constraints.yaml.
        </div>
      ) : (
        <div className="bg-[var(--panel)] border border-[var(--border)] divide-y divide-[var(--hairline-2)]">
          {constraints.map((c) => (
            <ConstraintCard key={c.id} constraint={c} />
          ))}
        </div>
      )}
    </section>
  );
}

function ConstraintCard({ constraint: c }: { constraint: SupplyConstraint }) {
  return (
    <div className="px-4 py-3 space-y-2">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <span className="font-medium">{c.market}</span>
          <span className="text-[var(--muted-foreground)] ml-2 text-[11px]">
            {c.deficit_pct === null
              ? "no credible projected deficit"
              : `deficit ${(c.deficit_pct * 100).toFixed(1)}%`}
            {" · "}
            {c.deficit_horizon}
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider">
          <ConfidencePill confidence={c.confidence} />
          {c.is_stale && (
            <span className="px-1.5 py-0.5 border border-[var(--warning)] text-[var(--warning)]">
              stale
            </span>
          )}
          <span className="text-[var(--muted-2)]">reviewed {c.last_reviewed}</span>
        </div>
      </div>

      <p className="text-[11px] text-[var(--muted-foreground)] normal-case">
        <span className="text-[var(--muted-2)] uppercase tracking-wider text-[10px] mr-1">source</span>
        {c.deficit_source}
      </p>
      <p className="text-[11px] text-[var(--muted-foreground)] normal-case">
        <span className="text-[var(--muted-2)] uppercase tracking-wider text-[10px] mr-1">lead time</span>
        {c.expansion_lead_months === null ? "n/a — no deficit to close" : `${c.expansion_lead_months} months expansion lead`}
        {" · "}
        <span className="text-[var(--muted-2)] uppercase tracking-wider text-[10px] mr-1 ml-2">driver</span>
        {c.demand_driver}
      </p>
      {c.counter_evidence && (
        <p className="text-[11px] text-[var(--warning)] normal-case border-l-2 border-[var(--warning)]/40 pl-2">
          <span className="text-[var(--muted-2)] uppercase tracking-wider text-[10px] mr-1 block">
            counter-evidence (why confidence is {c.confidence})
          </span>
          {c.counter_evidence}
        </p>
      )}

      <table className="w-full text-[11px] font-mono-jb mt-2">
        <thead>
          <tr className="text-left uppercase tracking-wider text-[10px] text-[var(--muted-2)]">
            <Th>Exposure</Th>
            <Th right>Revenue %</Th>
            <Th>Pure play</Th>
            <Th>Source</Th>
          </tr>
        </thead>
        <tbody>
          {c.exposures.map((e) => (
            <tr key={e.ticker} className="border-t border-[var(--hairline-2)]">
              <Td className="font-medium">{e.ticker}</Td>
              <Td right>{(e.revenue_exposure_pct * 100).toFixed(0)}%</Td>
              <Td>{e.is_pure_play ? "yes" : "no"}</Td>
              <Td className="text-[var(--muted-foreground)] normal-case max-w-[40ch]">
                {e.exposure_source}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfidencePill({ confidence }: { confidence: "high" | "medium" | "low" }) {
  const color =
    confidence === "high"
      ? "text-[var(--positive)] border-[var(--positive)]"
      : confidence === "medium"
        ? "text-[var(--info)] border-[var(--info)]"
        : "text-[var(--muted-foreground)] border-[var(--border)]";
  return <span className={cn("px-1.5 py-0.5 border", color)}>{confidence}</span>;
}

// ---------------------------------------------------------------------------
// Section 3: candidate detail

function CandidateDetail({ ticker }: { ticker: string | null }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["supply-margin-history", ticker],
    queryFn: () => api.supply.marginHistory(ticker as string),
    enabled: !!ticker,
  });

  return (
    <section>
      <header className="mb-3">
        <h2 className="text-sm tracking-tight">Candidate detail</h2>
        <p className="text-[11px] text-[var(--muted-foreground)] mt-1 normal-case">
          Quarterly gross-margin history — no price data. Click a row in the
          ranked watchlist above to load a candidate here.
        </p>
      </header>

      <div className="bg-[var(--panel)] border border-[var(--border)] px-4 py-4">
        {!ticker && (
          <p className="text-xs text-[var(--muted-foreground)] normal-case">
            No candidate selected yet.
          </p>
        )}
        {ticker && isLoading && (
          <p className="text-xs text-[var(--muted-foreground)] normal-case">
            Loading {ticker}…
          </p>
        )}
        {ticker && error && (
          <ErrorBanner error={error} fallback={`Could not load margin history for ${ticker}.`} />
        )}
        {ticker && data && (
          <>
            <h3 className="text-sm font-medium mb-3">{ticker}</h3>
            <MarginHistoryChart history={data} />
          </>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Shared bits

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cn("py-2 px-2.5 whitespace-nowrap", right && "text-right")}>{children}</th>;
}

function Td({
  children,
  right,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  className?: string;
}) {
  return (
    <td className={cn("py-2 px-2.5 whitespace-nowrap tabular-nums", right && "text-right", className)}>
      {children}
    </td>
  );
}

function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function ErrorBanner({ error, fallback }: { error: unknown; fallback: string }) {
  return (
    <div className="border border-[var(--negative)]/30 bg-[var(--negative)]/5 px-4 py-3 text-[11px] text-[var(--negative)] normal-case">
      {error instanceof ApiError && error.message ? error.message : fallback}
    </div>
  );
}
