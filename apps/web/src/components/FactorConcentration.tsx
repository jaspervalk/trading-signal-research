"use client";

import type { PolicyViewOut } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The headline metric: how much of the portfolio hangs on one driver.
 *
 * A stacked bar rather than a donut, deliberately. The question this answers is
 * "am I above or below the ceiling", which is a comparison against a line, and
 * people read positions along a shared axis far more accurately than arc
 * lengths. The ceiling is drawn on the bar itself so the gap is a distance you
 * can see rather than two numbers you have to subtract.
 */

/** Palette ordered so the AI buckets read as one block, then everything else. */
const FACTOR_COLOR: Record<string, string> = {
  AI_PLATFORM: "#22d3ee",
  AI_CAPEX: "#0ea5e9",
  AI_COMPONENTS: "#6366f1",
  AI_POWER: "#8b5cf6",
  DEFENSE: "#f59e0b",
  SPACE: "#f97316",
  MATERIALS: "#a16207",
  HEALTHCARE: "#22c55e",
  FINTECH: "#14b8a6",
  CASH: "#52525b",
  UNMAPPED: "#3f3f46",
};

export function FactorConcentration({ policy }: { policy: PolicyViewOut }) {
  if (!policy.available) return null;

  const ai = policy.ai_weight * 100;
  const ceiling = policy.ai_target_max * 100;
  const over = policy.ai_excess_pp > 0;

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)] flex items-baseline justify-between gap-3">
        <h2 className="text-sm tracking-tight">Factor concentration</h2>
        <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-2)]">
          computed · grouped by shared drawdown driver
        </span>
      </header>

      <div className="px-4 py-4 space-y-4">
        <div className="flex items-end gap-3 flex-wrap">
          <span
            className={cn(
              "text-5xl font-semibold tabular-nums leading-none",
              over ? "text-[var(--warning)]" : "text-[var(--positive)]",
            )}
          >
            {ai.toFixed(1)}%
          </span>
          <div className="pb-1">
            <p className="text-[13px]">AI-related</p>
            <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
              ceiling {ceiling.toFixed(0)}% ·{" "}
              <span className={over ? "text-[var(--warning)]" : "text-[var(--positive)]"}>
                {policy.ai_excess_pp > 0 ? "+" : ""}
                {policy.ai_excess_pp.toFixed(1)}pp
              </span>
            </p>
          </div>
        </div>

        <StackedBar factors={policy.factors} ceiling={policy.ai_target_max} />

        <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
          {policy.factors.map((f) => (
            <li key={f.name} className="flex items-center gap-2">
              <span
                aria-hidden
                className="inline-block w-2 h-2 shrink-0"
                style={{ background: FACTOR_COLOR[f.name] ?? FACTOR_COLOR.UNMAPPED }}
              />
              <span className="text-[var(--muted-foreground)] truncate">
                {f.name.replace(/_/g, " ").toLowerCase()}
              </span>
              <span className="ml-auto tabular-nums">{(f.weight * 100).toFixed(1)}%</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function StackedBar({
  factors,
  ceiling,
}: {
  factors: PolicyViewOut["factors"];
  ceiling: number;
}) {
  let offset = 0;
  return (
    <div className="space-y-1.5">
      <div className="relative h-8 w-full bg-[var(--panel-2)] overflow-hidden">
        {factors.map((f) => {
          const left = offset;
          offset += f.weight;
          return (
            <div
              key={f.name}
              className="absolute inset-y-0"
              style={{
                left: `${left * 100}%`,
                width: `${f.weight * 100}%`,
                background: FACTOR_COLOR[f.name] ?? FACTOR_COLOR.UNMAPPED,
              }}
              title={`${f.name} ${(f.weight * 100).toFixed(1)}%`}
            />
          );
        })}
        {/* The ceiling sits on the bar so the overshoot is a visible distance. */}
        <div
          className="absolute inset-y-0 w-px bg-[var(--foreground)]"
          style={{ left: `${ceiling * 100}%` }}
          aria-hidden
        />
      </div>
      <div className="relative h-3">
        <span
          className="absolute text-[10px] uppercase tracking-wider text-[var(--muted-foreground)] -translate-x-1/2 whitespace-nowrap"
          style={{ left: `${ceiling * 100}%` }}
        >
          {(ceiling * 100).toFixed(0)}% ceiling
        </span>
      </div>
    </div>
  );
}
