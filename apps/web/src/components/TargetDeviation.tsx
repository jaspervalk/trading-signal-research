"use client";

import type { PolicyPositionOut, PolicyViewOut } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Deviation from target weight, most underweight first.
 *
 * Diverging bars from a shared zero line, so over and under are read as
 * direction rather than as two lists. The five most underweight carry a rank
 * marker: that is the buy order, and it is the only ordering on the page that
 * is meant to be acted on.
 *
 * Positions without a target are shown but dimmed, and never given a bar. A
 * holding with no thesis is not "0% underweight"; it is outside the policy, and
 * drawing it on the same axis would imply a target that was never set.
 */
export function TargetDeviation({ policy }: { policy: PolicyViewOut }) {
  if (!policy.available) return null;

  const targeted = policy.positions.filter((p) => p.deviation_pp !== null);
  const untargeted = policy.positions.filter((p) => p.deviation_pp === null);
  const ordered = [...targeted].sort(
    (a, b) => (a.deviation_pp ?? 0) - (b.deviation_pp ?? 0),
  );
  const buySet = new Set(policy.buy_order.slice(0, 5));
  const span = Math.max(
    5,
    ...ordered.map((p) => Math.abs(p.deviation_pp ?? 0)),
  );

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)] flex items-baseline justify-between gap-3">
        <h2 className="text-sm tracking-tight">Deviation from target</h2>
        <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-2)]">
          computed · ranked = this month&apos;s buy order
        </span>
      </header>

      <div className="px-4 py-3 space-y-1">
        {ordered.map((p) => (
          <Row key={p.ticker} p={p} span={span} rank={rankOf(p, policy)} inBuy={buySet.has(p.ticker)} />
        ))}
      </div>

      {untargeted.length > 0 && (
        <div className="px-4 py-3 border-t border-[var(--hairline-2)]">
          <p className="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)] mb-1.5">
            Held without a target
          </p>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-[var(--muted-2)]">
            {untargeted.map((p) => (
              <li key={p.ticker} className="flex items-baseline gap-2">
                <span className="text-[var(--muted-foreground)]">{p.ticker}</span>
                <span className="tabular-nums">{(p.weight * 100).toFixed(1)}%</span>
                <span className="ml-auto">{(p.status ?? "unclassified").replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function rankOf(p: PolicyPositionOut, policy: PolicyViewOut): number | null {
  const i = policy.buy_order.indexOf(p.ticker);
  return i >= 0 && i < 5 ? i + 1 : null;
}

function Row({
  p,
  span,
  rank,
  inBuy,
}: {
  p: PolicyPositionOut;
  span: number;
  rank: number | null;
  inBuy: boolean;
}) {
  const dev = p.deviation_pp ?? 0;
  const frac = Math.min(1, Math.abs(dev) / span);
  const over = dev > 0;
  const color =
    p.band_status === "trim"
      ? "var(--warning)"
      : p.band_status === "add"
        ? "var(--info)"
        : "var(--muted-2)";

  return (
    <div className="flex items-center gap-2 text-[11px] h-6">
      <span className="w-4 text-right text-[var(--muted-2)] tabular-nums">
        {rank ?? ""}
      </span>
      <span className={cn("w-16 truncate", inBuy && "text-[var(--foreground)]")}>
        {p.ticker}
      </span>

      <div className="relative flex-1 h-3">
        <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--border)]" aria-hidden />
        <div
          className="absolute inset-y-0"
          style={{
            background: color,
            width: `${(frac * 50).toFixed(2)}%`,
            left: over ? "50%" : `${(50 - frac * 50).toFixed(2)}%`,
          }}
          title={`${p.ticker} ${dev > 0 ? "+" : ""}${dev.toFixed(1)}pp`}
        />
      </div>

      <span className="w-14 text-right tabular-nums text-[var(--muted-foreground)]">
        {(p.weight * 100).toFixed(1)}%
      </span>
      <span
        className="w-16 text-right tabular-nums"
        style={{ color: p.band_status === "in_band" ? "var(--muted-2)" : color }}
      >
        {dev > 0 ? "+" : ""}
        {dev.toFixed(1)}pp
      </span>
    </div>
  );
}
