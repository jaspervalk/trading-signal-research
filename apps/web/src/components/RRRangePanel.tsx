"use client";

import type { RRDistribution } from "@/lib/api";

type Props = {
  distribution: RRDistribution | null;
  chosenRR: number | null;
};

/**
 * Range bar showing min..max R/R across all candidate level pairings,
 * with the chosen R/R marked. Honest about how much the headline
 * single-number R/R depends on which candidate combo was picked.
 *
 * Renders nothing for legacy plans where r_r_distribution is null.
 */
export function RRRangePanel({ distribution, chosenRR }: Props) {
  if (!distribution || distribution.n_combos === 0) {
    return null;
  }
  const { min_rr, median_rr, max_rr, n_combos } = distribution;
  const span = Math.max(max_rr - min_rr, 0.01);
  const chosenPct =
    chosenRR === null ? null : ((chosenRR - min_rr) / span) * 100;
  const medianPct = ((median_rr - min_rr) / span) * 100;

  return (
    <div className="text-[11px] font-mono-jb text-[var(--muted-2)] space-y-1">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="uppercase tracking-wider">R/R range</span>
        <span className="tabular-nums">
          {min_rr.toFixed(2)} – {max_rr.toFixed(2)}
          <span className="text-[var(--muted-foreground)]">
            {" · "}median {median_rr.toFixed(2)}
          </span>
          <span className="text-[var(--muted-foreground)]">
            {" · "}n={n_combos}
          </span>
        </span>
      </div>
      <div className="relative h-2 bg-[var(--hairline-2)] rounded">
        <div
          className="absolute top-0 h-2 w-px bg-[var(--muted-foreground)]"
          style={{ left: `${medianPct}%` }}
          title={`median R/R ${median_rr.toFixed(2)}`}
        />
        {chosenPct !== null && (
          <div
            className="absolute top-[-2px] h-3 w-1 bg-[var(--warning)]"
            style={{ left: `${chosenPct}%` }}
            title={`chosen R/R ${chosenRR?.toFixed(2)}`}
          />
        )}
      </div>
      <div className="text-[10px] opacity-70">
        Chosen R/R is one of {n_combos} candidate-level pairings; range shown
        for context.
      </div>
    </div>
  );
}
