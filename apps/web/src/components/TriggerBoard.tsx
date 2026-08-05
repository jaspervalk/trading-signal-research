"use client";

import type { PolicyViewOut, TriggerOut } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Invalidation conditions per position, with the next date something checkable
 * lands.
 *
 * A table, not a chart: these are sentences, and the value is in reading them
 * before a report rather than after. Status is set by hand in the policy file
 * and never inferred, because a condition like "GPU depreciation term changed"
 * has no data feed and a guessed status would be worse than none.
 *
 * Rows with a report date inside the next fortnight sort first, since the whole
 * point is to reread the condition before the event rather than after it.
 */
export function TriggerBoard({ policy }: { policy: PolicyViewOut }) {
  if (policy.triggers.length === 0) return null;

  const today = new Date();
  const soon = new Date(today.getTime() + 14 * 86400_000);

  const withDates = policy.triggers
    .filter((t) => t.next_report)
    .sort((a, b) => (a.next_report ?? "").localeCompare(b.next_report ?? ""));
  const withoutDates = policy.triggers.filter((t) => !t.next_report);
  const imminent = withDates.filter(
    (t) => new Date(t.next_report as string) <= soon,
  );

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)] flex items-baseline justify-between gap-3 flex-wrap">
        <h2 className="text-sm tracking-tight">Trigger board</h2>
        <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-2)]">
          yours · edit configs/portfolio_policy.yaml
        </span>
      </header>

      {imminent.length > 0 && (
        <p className="px-4 py-2 border-b border-[var(--hairline-2)] text-[11px] text-[var(--warning)]">
          {imminent.map((t) => `${t.ticker} ${fmt(t.next_report)}`).join(" · ")} — reread
          before the print, not after.
        </p>
      )}

      <div className="divide-y divide-[var(--hairline-2)]">
        {[...withDates, ...withoutDates].map((t) => (
          <Row key={t.ticker} t={t} />
        ))}
      </div>
    </section>
  );
}

function Row({ t }: { t: TriggerOut }) {
  return (
    <div className="px-4 py-2.5 grid grid-cols-[4.5rem_1fr_5rem] gap-3 items-baseline">
      <span className="text-[12px]">{t.ticker}</span>
      <p className="text-[11px] leading-relaxed text-[var(--muted-foreground)] max-w-[70ch]">
        {t.condition}
      </p>
      <div className="text-right space-y-0.5">
        <StatusPill status={t.status} />
        {t.next_report && (
          <div className="text-[10px] uppercase tracking-wider text-[var(--muted-2)] tabular-nums">
            {fmt(t.next_report)}
          </div>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-block px-1.5 py-0.5 text-[10px] uppercase tracking-wider border",
        status === "fired" &&
          "text-[var(--negative)] border-[var(--negative)]",
        status === "watch" && "text-[var(--warning)] border-[var(--warning)]",
        status === "ok" && "text-[var(--muted-2)] border-[var(--border)]",
      )}
    >
      {status}
    </span>
  );
}

function fmt(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
