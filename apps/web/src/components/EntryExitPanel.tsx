"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { LensPanel } from "@/components/LensPanel";
import { Provenance } from "@/components/Provenance";
import { RRRangePanel } from "@/components/RRRangePanel";
import { api, type EntryExitPlan, type ResearchZoneBand } from "@/lib/api";
import { cn, fmtDateTime } from "@/lib/utils";

/**
 * Entry / Exit Research panel — unified trade ticket + multi-lens analyst panel.
 *
 * Layout (top → bottom):
 *  1. Header with Quick / Deep buttons + cost.
 *  2. Trade Setup block — entry / pullback / stop / T1 / T2 with Plan R/R headline.
 *  3. Bull / Bear / Risks (LLM-driven prose).
 *  4. Multi-lens analyst panel (4 lenses · independent reads).
 *  5. Audit footer + disclaimer.
 *
 * Plan R/R is the headline number; per-tier R/R sits next to each exit row
 * for transparency. See docs/entry-exit-research-plan.md and the Q3
 * R/R-methodology answer.
 */
export function EntryExitPanel({ ticker }: { ticker: string }) {
  const qc = useQueryClient();
  const quickQ = useQuery({
    queryKey: ["research-quick-cached", ticker],
    queryFn: () => api.research.quickGet(ticker),
  });
  const deepQ = useQuery({
    queryKey: ["research-deep-cached", ticker],
    queryFn: () => api.research.deepGet(ticker),
  });

  const runQuick = useMutation({
    mutationFn: ({ force }: { force?: boolean }) =>
      api.research.quickRun(ticker, force ?? false),
    onSuccess: (plan) => {
      qc.setQueryData(["research-quick-cached", ticker], plan);
    },
  });
  const runDeep = useMutation({
    mutationFn: ({ force }: { force?: boolean }) =>
      api.research.deepRun(ticker, force ?? false),
    onSuccess: (plan) => {
      qc.setQueryData(["research-deep-cached", ticker], plan);
    },
  });

  // Prefer the Deep plan when available — it carries more reasoning and was
  // explicitly requested by the user, so it should overshadow Quick once
  // computed for the same ticker / day.
  const plan = deepQ.data ?? quickQ.data ?? null;
  const isLoading = quickQ.isLoading || deepQ.isLoading || runQuick.isPending || runDeep.isPending;
  const error = (quickQ.error || deepQ.error || runQuick.error || runDeep.error) as Error | null;

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)] flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-baseline gap-2 flex-wrap">
            <h2 className="text-sm tracking-tight">Entry / Exit research</h2>
            <Provenance
              kind="model"
              detail={plan ? `$${plan.cost_usd.toFixed(3)}` : undefined}
            />
          </div>
          <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
            {plan
              ? `${plan.mode.toUpperCase()} · ${plan.confidence} confidence · ${plan.timeframe}`
              : "Run Quick (~$0.01) or Deep (4 agents + judge, ~$0.10)."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => runQuick.mutate({ force: !!quickQ.data })}
            disabled={runQuick.isPending || runDeep.isPending}
            className="text-[11px] uppercase tracking-wider px-2.5 py-1 border border-[var(--info)] text-[var(--info)] hover:bg-[color:rgba(34,211,238,0.10)] disabled:opacity-30"
            title={quickQ.data ? "Force a fresh quick run, bypassing today's cache" : "Run quick research"}
          >
            {runQuick.isPending
              ? "Running…"
              : quickQ.data
                ? "Re-run quick · ~$0.01"
                : "⚡ Quick · ~$0.01"}
          </button>
          <button
            onClick={() => runDeep.mutate({ force: !!deepQ.data })}
            disabled={runQuick.isPending || runDeep.isPending}
            className="text-[11px] uppercase tracking-wider px-2.5 py-1 border border-[var(--positive)] text-[var(--positive)] hover:bg-[color:rgba(34,197,94,0.10)] disabled:opacity-30"
            title="4 parallel Haiku analysts + Sonnet judge · ~30-45s · ~$0.10"
          >
            {runDeep.isPending
              ? "Running 4 agents…"
              : deepQ.data
                ? "Re-run deep · ~$0.10"
                : "🔍 Deep · ~$0.10"}
          </button>
        </div>
      </header>

      {error && (
        <div className="px-4 py-3 text-[11px] text-[var(--negative)]">
          {error.message}
        </div>
      )}

      {!plan && !isLoading && !error && (
        <div className="px-4 py-6 text-xs text-[var(--muted-foreground)]">
          No plan yet. Click <span className="text-[var(--info)]">Quick read</span> above.
        </div>
      )}

      {plan && <PlanBody plan={plan} />}
    </section>
  );
}

function PlanBody({ plan }: { plan: EntryExitPlan }) {
  return (
    <div>
      <TradeTicket plan={plan} />

      <div className="p-4 space-y-4 text-xs border-t border-[var(--hairline-2)]">
        <CaseList title="Bull case" items={plan.bull_case} accent="text-[var(--positive)]" />
        <CaseList title="Bear case" items={plan.bear_case} accent="text-[var(--negative)]" />
        <CaseList title="Key risks" items={plan.key_risks} accent="text-[var(--warning)]" />
      </div>

      {plan.lenses && plan.lenses.length > 0 && (
        <div className="border-t border-[var(--hairline-2)]">
          <LensPanel lenses={plan.lenses} />
        </div>
      )}

      <footer className="px-4 py-3 border-t border-[var(--hairline-2)] text-[11px] uppercase tracking-wider text-[var(--muted-2)] flex flex-wrap gap-x-4 gap-y-1">
        <span>mode {plan.mode}</span>
        <span>conf {plan.confidence}</span>
        <span>cost ${plan.cost_usd.toFixed(4)}</span>
        <span>{plan.duration_ms}ms</span>
        <span>sources [{plan.sources_used.join(", ")}]</span>
        <span>as of {fmtDateTime(plan.as_of)}</span>
      </footer>

      <p className="px-4 pb-3 text-[11px] italic text-[var(--muted-2)]">{plan.disclaimer}</p>
    </div>
  );
}

/**
 * Unified trade-ticket block — entry / pullback / stop / targets / Plan R/R.
 * Aligned columns so the trader can read it like a trade ticket at a glance.
 * Plan R/R is the headline (sized 2xl, color-coded). Per-tier R/R is shown
 * inline next to each target for transparency.
 */
function TradeTicket({ plan }: { plan: EntryExitPlan }) {
  const planRR = plan.plan_r_r_blended;
  return (
    <div className="px-4 py-4 border-b border-[var(--hairline-2)] bg-[var(--panel-2)]">
      <div className="flex items-baseline justify-between gap-3 mb-3 flex-wrap">
        <span className="text-[11px] uppercase tracking-[0.14em] text-[var(--muted-2)]">
          Trade setup
        </span>
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
            Plan R/R
          </span>
          <span
            className={cn(
              "text-2xl tabular-nums tracking-tight",
              planRRColor(planRR),
            )}
          >
            {planRR === null ? "—" : planRR.toFixed(2)}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-[var(--muted-2)]">
            (1/3 T1 · 2/3 T2)
          </span>
        </div>
      </div>

      <div className="space-y-2 text-xs">
        <TicketRow
          label="Entry"
          band={plan.entry_zone}
          tone="text-[var(--info)]"
        />
        {plan.pullback_entry_zone && (
          <TicketRow
            label="Pullback entry"
            band={plan.pullback_entry_zone}
            tone="text-[var(--info)]"
            note="risk-managed alt"
          />
        )}
        <StopRow value={plan.invalidation} />
        <TicketRow
          label="Target 1 (trim)"
          band={plan.exit_zone_primary}
          tone="text-[var(--positive)]"
          rr={plan.risk_reward_primary}
        />
        {plan.exit_zone_runner && (
          <TicketRow
            label="Target 2 (runner)"
            band={plan.exit_zone_runner}
            tone="text-[var(--positive)]"
            rr={plan.risk_reward_runner}
          />
        )}
      </div>

      {planRR !== null && (
        <div className="mt-3 pt-2 border-t border-[var(--hairline-2)]">
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-[var(--hairline-2)] relative overflow-hidden">
              <div
                className={cn("absolute left-0 top-0 h-full", planRRBar(planRR))}
                style={{ width: `${Math.min(100, (planRR / 3.0) * 100)}%` }}
              />
            </div>
            <span className="text-[10px] uppercase tracking-wider text-[var(--muted-2)]">
              {planRRLabel(planRR)}
            </span>
          </div>
        </div>
      )}

      {plan.r_r_distribution && plan.r_r_distribution.n_combos > 0 && (
        <div className="mt-3 pt-2 border-t border-[var(--hairline-2)]">
          <RRRangePanel
            distribution={plan.r_r_distribution}
            chosenRR={plan.plan_r_r_blended}
          />
        </div>
      )}
    </div>
  );
}

function TicketRow({
  label,
  band,
  tone,
  rr,
  note,
}: {
  label: string;
  band: ResearchZoneBand;
  tone: string;
  rr?: number | null;
  note?: string;
}) {
  return (
    <div>
      <div className="grid grid-cols-[180px_1fr_auto] gap-3 items-baseline">
        <span className="text-[var(--muted-foreground)]">{label}</span>
        <span className={cn("tabular-nums", tone)}>
          {band.low.toFixed(2)} – {band.high.toFixed(2)}
        </span>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-2)] tabular-nums">
          {rr !== undefined && rr !== null ? `R/R ${rr.toFixed(2)}` : ""}
          {note && (
            <span className="ml-2 text-[var(--muted-2)]">[{note}]</span>
          )}
        </span>
      </div>
      <div className="grid grid-cols-[180px_1fr_auto] gap-3 mt-0.5 text-[11px] text-[var(--muted-2)]">
        <span />
        <span className="italic">{band.method}</span>
        <span />
      </div>
    </div>
  );
}

function StopRow({ value }: { value: number }) {
  return (
    <div className="grid grid-cols-[180px_1fr_auto] gap-3 items-baseline">
      <span className="text-[var(--muted-foreground)]">Stop / invalidation</span>
      <span className="tabular-nums text-[var(--negative)]">{value.toFixed(2)}</span>
      <span className="text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
        below this = thesis broken
      </span>
    </div>
  );
}

function CaseList({
  title,
  items,
  accent,
}: {
  title: string;
  items: string[];
  accent: string;
}) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-[0.12em] text-[var(--muted-2)] mb-1.5">
        {title}
      </h3>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="text-[var(--foreground)]">
            <span className={cn("mr-1", accent)}>·</span>
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

function planRRColor(rr: number | null): string {
  if (rr === null) return "text-[var(--muted-foreground)]";
  if (rr < 1.0) return "text-[var(--negative)]";
  if (rr < 1.5) return "text-[var(--warning)]";
  if (rr < 2.5) return "text-[var(--positive)]";
  return "text-[var(--positive)]";
}

function planRRBar(rr: number): string {
  if (rr < 1.0) return "bg-[var(--negative)]";
  if (rr < 1.5) return "bg-[var(--warning)]";
  return "bg-[var(--positive)]";
}

function planRRLabel(rr: number): string {
  if (rr < 1.0) return "math doesn't favour you";
  if (rr < 1.5) return "marginal";
  if (rr < 2.5) return "standard swing";
  return "asymmetric";
}
