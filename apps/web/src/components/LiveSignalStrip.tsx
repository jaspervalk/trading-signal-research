"use client";

import { useQuery } from "@tanstack/react-query";

import { SignalTile } from "@/components/SignalTile";
import { api, type TickerSignal } from "@/lib/api";

const WINDOWS: Array<"7d" | "30d"> = ["7d", "30d"];
const SIGNAL_TYPES: Array<"claims_all" | "claims_factual" | "creator_consensus"> = [
  "claims_all",
  "claims_factual",
  "creator_consensus",
];

/**
 * IA section 2: 6-tile signal strip (windows × signal types).
 *
 * The endpoint can return multiple historical rows per (window, signal_type);
 * we pick the most recent per slot (rows arrive sorted by window_end desc).
 */
export function LiveSignalStrip({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticker-signals", ticker],
    queryFn: () => api.tickers.signals(ticker),
  });

  const lookup = pickLatestPerSlot(data ?? []);

  return (
    <section>
      <h2 className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mb-2 font-mono-jb">
        Live signal · 7d / 30d
      </h2>
      <div className="grid grid-cols-2 gap-2">
        {SIGNAL_TYPES.flatMap((type) =>
          WINDOWS.map((win) => (
            <SignalTile
              key={`${win}-${type}`}
              windowSize={win}
              signalType={type}
              signal={lookup[`${win}-${type}`] ?? null}
            />
          )),
        )}
      </div>
      {isLoading && (
        <p className="text-[11px] uppercase tracking-wider text-[var(--muted-2)] mt-2 font-mono-jb">
          Loading signals…
        </p>
      )}
      {error && (
        <p className="text-[11px] uppercase tracking-wider text-[var(--negative)] mt-2 font-mono-jb">
          Signals API error · run aggregate-signals
        </p>
      )}
    </section>
  );
}

function pickLatestPerSlot(
  rows: TickerSignal[],
): Record<string, TickerSignal> {
  // Rows arrive sorted by window_end desc. Take the first hit per slot.
  const out: Record<string, TickerSignal> = {};
  for (const r of rows) {
    const key = `${r.window_size}-${r.signal_type}`;
    if (!(key in out)) out[key] = r;
  }
  return out;
}
