import type { ResearchEntryZone } from "@/lib/api";
import { fmtPct } from "@/lib/utils";

/**
 * Entry-zone candidate. Decision-support framing only — every label says
 * "research zone" / "invalidation reference" / "risk reference". Never
 * "stop loss" / "target" / "buy".
 */
export function EntryZoneCard({ entry }: { entry: ResearchEntryZone }) {
  if (!entry.available) {
    return (
      <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
        <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
          <h2 className="text-sm tracking-tight">Entry zone</h2>
          <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
            [STATUS: NOT_AVAILABLE]
          </p>
        </header>
        <div className="p-4 text-[12px] text-[var(--muted-foreground)]">
          {entry.reason_unavailable ?? "No applicable rule for this setup."}
        </div>
      </section>
    );
  }

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm tracking-tight">Entry zone candidate</h2>
        <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          [STATUS: AVAILABLE] [R/R: {fmtNum(entry.risk_reward_estimate)}]
        </p>
      </header>
      <div className="p-4 grid grid-cols-2 gap-x-6 gap-y-2 text-[12px]">
        <Field label="trigger" value={fmtNum(entry.setup_trigger_level)} />
        <Field label="resistance" value={fmtNum(entry.nearest_resistance)} />
        <Field
          label="research zone"
          value={`${fmtNum(entry.candidate_research_zone_low)} – ${fmtNum(entry.candidate_research_zone_high)}`}
        />
        <Field label="invalidation" value={fmtNum(entry.invalidation_reference)} />
        <Field
          label="risk reference"
          value={`${fmtPct(entry.risk_reference_pct)} · ${fmtNum(entry.risk_reference_atrs)} ATR`}
        />
        <Field
          label="R/R estimate"
          value={fmtNum(entry.risk_reward_estimate)}
          color="text-[var(--info)]"
        />
      </div>
      {entry.method_notes.length > 0 && (
        <div className="px-4 pb-4 text-[11px] text-[var(--muted-foreground)] space-y-1 border-t border-[var(--hairline-2)] pt-3">
          {entry.method_notes.map((n, i) => (
            <p key={i}>· {n}</p>
          ))}
        </div>
      )}
      <p className="px-4 pb-3 text-[11px] italic text-[var(--muted-2)]">
        {entry.language_disclaimer}
      </p>
    </section>
  );
}

function Field({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--muted-2)]">
        {label}
      </div>
      <div className={`tabular-nums text-[14px] ${color ?? ""}`}>{value}</div>
    </div>
  );
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}
