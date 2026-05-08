import type { ResearchRubricEntry, ResearchSetup, ResearchStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Setup classification + decision rubric — center column. The "show your work"
 * surface for the headline status. Bracketed metadata, no composite scores.
 */
export function SetupCard({
  setup,
  status,
}: {
  setup: ResearchSetup;
  status: ResearchStatus;
}) {
  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm tracking-tight">
          Setup · {setup.setup_type.replace(/_/g, " ")}
        </h2>
        <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          [DATA: {setup.data_sufficiency}] [CONF: {setup.confidence}]
        </p>
      </header>
      <div className="p-4 space-y-4 text-[12px]">
        {setup.reasons.length > 0 && (
          <div>
            <h3 className="text-[11px] uppercase tracking-[0.12em] text-[var(--muted-2)] mb-1.5">
              Reasons
            </h3>
            <ul className="space-y-1">
              {setup.reasons.map((r, i) => (
                <li key={i} className="text-[var(--foreground)]">
                  <span className="text-[var(--positive)]">+ </span>
                  {r}
                </li>
              ))}
            </ul>
          </div>
        )}
        {setup.counterarguments.length > 0 && (
          <div>
            <h3 className="text-[11px] uppercase tracking-[0.12em] text-[var(--muted-2)] mb-1.5">
              Counterarguments
            </h3>
            <ul className="space-y-1">
              {setup.counterarguments.map((c, i) => (
                <li key={i} className="text-[var(--muted-foreground)]">
                  <span className="text-[var(--warning)]">− </span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        )}
        {status.rubric.length > 0 && (
          <div>
            <h3 className="text-[11px] uppercase tracking-[0.12em] text-[var(--muted-2)] mb-1.5">
              Decision rubric
            </h3>
            <div className="space-y-0.5">
              {status.rubric.map((r) => (
                <RubricRow key={r.name} entry={r} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function RubricRow({ entry }: { entry: ResearchRubricEntry }) {
  const passed = entry.passed;
  const marker = passed === true ? "✓" : passed === false ? "✗" : "·";
  const markerColor =
    passed === true
      ? "text-[var(--positive)]"
      : passed === false
        ? "text-[var(--negative)]"
        : "text-[var(--muted-2)]";
  return (
    <div className="grid grid-cols-[14px_minmax(0,1fr)_auto] gap-2 items-baseline text-[11px]">
      <span className={cn("font-bold", markerColor)}>{marker}</span>
      <span className="truncate text-[var(--foreground)]">{entry.name}</span>
      <span className="text-[var(--muted-foreground)] tabular-nums whitespace-nowrap">
        [{fmtVal(entry.value)} / thr {fmtVal(entry.threshold)}] [w:{entry.weight}]
      </span>
    </div>
  );
}

function fmtVal(v: number | string | null): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(2);
  return v;
}
