"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { ScanStatusBoard } from "@/components/ScanStatusBoard";
import { api, type WatchlistEntry } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

export default function WatchlistPage() {
  const qc = useQueryClient();

  const { data: watchlist } = useQuery({
    queryKey: ["watchlist", "all"],
    queryFn: () => api.watchlist.list(),
  });

  const tickerEntries = (watchlist ?? []).filter((w) => w.entity_type === "ticker");
  const nonTicker = (watchlist ?? []).filter((w) => w.entity_type !== "ticker");

  // Run the scan against the watchlist (server resolves the list — same
  // API the CLI hits via `tsr scan --watchlist`).
  const {
    data: scan,
    isLoading: scanLoading,
    error: scanError,
    refetch: refetchScan,
  } = useQuery({
    queryKey: ["research-scan", "watchlist"],
    queryFn: () => api.research.scan({ source: "watchlist", fetch_metadata: false }),
    enabled: tickerEntries.length > 0,
    staleTime: 60_000,
  });

  const unpin = useMutation({
    mutationFn: (id: number) => api.watchlist.unpin(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlist"] });
      qc.invalidateQueries({ queryKey: ["research-scan", "watchlist"] });
    },
  });

  return (
    <div className="space-y-6 font-mono-jb">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1.5">
          Pinned tickers ranked by current research status. Star (★) any ticker page to add.
        </p>
      </div>

      <section>
        <header className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm tracking-tight">
              Status board · {tickerEntries.length} ticker{tickerEntries.length === 1 ? "" : "s"}
            </h2>
            {scan && (
              <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
                research_candidates={scan.n_research_candidates} · watch={scan.n_watch} ·
                skip={scan.n_skip} · errors={scan.n_errors}
              </p>
            )}
          </div>
          <button
            onClick={() => refetchScan()}
            disabled={scanLoading || tickerEntries.length === 0}
            className="text-[11px] uppercase tracking-wider px-2.5 py-1 border border-[var(--border)] hover:bg-[var(--muted)] disabled:opacity-30"
          >
            {scanLoading ? "Scanning…" : "Re-scan"}
          </button>
        </header>

        {tickerEntries.length === 0 && (
          <div className="text-xs text-[var(--muted-foreground)] px-4 py-3 border border-[var(--border)] bg-[var(--panel)]">
            No tickers pinned yet. The ★ button on any ticker page adds it here.
          </div>
        )}

        {scanError && (
          <div className="text-xs text-[var(--negative)] px-4 py-3 border border-[var(--border)] bg-[var(--panel)]">
            Scan API error · {(scanError as Error).message}
          </div>
        )}

        {scan && <ScanStatusBoard rows={scan.rows} />}

        {tickerEntries.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {tickerEntries.map((w) => (
              <button
                key={w.id}
                onClick={() => unpin.mutate(w.id)}
                className="text-[11px] uppercase tracking-wider px-2 py-0.5 border border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--negative)]/10 hover:text-[var(--negative)]"
                title={`Pinned ${fmtDate(w.pinned_at)} — click to unpin`}
              >
                {w.entity_id} ×
              </button>
            ))}
          </div>
        )}
      </section>

      {nonTicker.length > 0 && (
        <section>
          <h2 className="text-sm tracking-tight mb-3">Other pinned</h2>
          <ul className="bg-[var(--panel)] border border-[var(--border)] divide-y divide-[var(--hairline-2)]">
            {nonTicker.map((w) => (
              <NonTickerRow key={w.id} entry={w} onUnpin={(id) => unpin.mutate(id)} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function NonTickerRow({
  entry,
  onUnpin,
}: {
  entry: WatchlistEntry;
  onUnpin: (id: number) => void;
}) {
  const href =
    entry.entity_type === "creator"
      ? `/creators/${entry.entity_id}`
      : `/calls/${entry.entity_id}`;
  return (
    <li className="px-4 py-2.5 flex items-center justify-between text-xs">
      <Link href={href} className="hover:text-[var(--info)]">
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mr-2">
          [{entry.entity_type}]
        </span>
        {entry.entity_type === "creator" ? `creator #${entry.entity_id}` : `call #${entry.entity_id}`}
      </Link>
      <div className="flex items-center gap-3 text-[var(--muted-foreground)]">
        <span>{fmtDate(entry.pinned_at)}</span>
        <button onClick={() => onUnpin(entry.id)} className="hover:text-[var(--negative)]">
          unpin
        </button>
      </div>
    </li>
  );
}
