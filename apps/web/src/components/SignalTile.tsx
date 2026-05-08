import { PolarityBar } from "@/components/PolarityBar";
import type { TickerSignal } from "@/lib/api";
import { cn, fmtSigned } from "@/lib/utils";

/**
 * A single tile in the LiveSignalStrip. Encodes one (window_size × signal_type) cell
 * of the IA spec: bracketed corner metadata, big credibility-weighted polarity,
 * polarity bar, raw polarity + creator count in fineprint.
 *
 * `signal === null` renders the empty state ("no signal · N=0") per IA section 2.
 */
export function SignalTile({
  windowSize,
  signalType,
  signal,
}: {
  windowSize: "7d" | "30d";
  signalType: "claims_all" | "claims_factual" | "creator_consensus";
  signal: TickerSignal | null;
}) {
  const empty = signal === null;
  const weighted = signal?.credibility_weighted_polarity ?? null;
  const raw = signal?.net_polarity ?? null;
  const n = signal?.n_mentions ?? 0;
  const creators = signal?.n_distinct_creators ?? 0;

  const valueColor =
    weighted === null
      ? "text-[var(--muted-foreground)]"
      : weighted > 0.001
        ? "text-[var(--positive)]"
        : weighted < -0.001
          ? "text-[var(--negative)]"
          : "text-[var(--foreground)]";

  return (
    <div className="font-mono-jb bg-[var(--panel)] border border-[var(--border)] p-3 flex flex-col gap-2">
      <div className="text-[11px] uppercase tracking-wider text-[var(--muted-2)] truncate">
        [{signalType}]
      </div>
      <div className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
        [N={n}] [W={windowSize}]
      </div>
      <div className={cn("text-2xl font-medium leading-none tracking-tight", valueColor)}>
        {empty ? "—" : fmtSigned(weighted)}
      </div>
      <PolarityBar value={weighted} />
      <div className="grid grid-cols-2 gap-2 text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
        <div className="flex flex-col gap-0.5">
          <span className="text-[var(--muted-2)]">creators</span>
          <span className="tabular-nums text-[var(--foreground)]">{creators}</span>
        </div>
        <div className="flex flex-col gap-0.5 text-right">
          <span className="text-[var(--muted-2)]">raw</span>
          <span className="tabular-nums text-[var(--foreground)]">
            {empty ? "—" : fmtSigned(raw)}
          </span>
        </div>
      </div>
      {empty && (
        <div className="text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
          no signal
        </div>
      )}
    </div>
  );
}
