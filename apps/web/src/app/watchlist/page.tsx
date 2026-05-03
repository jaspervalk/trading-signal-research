"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Card, CardHeader } from "@/components/Card";
import { api, type WatchlistEntry } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

export default function WatchlistPage() {
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["watchlist", "all"],
    queryFn: () => api.watchlist.list(),
  });

  const unpin = useMutation({
    mutationFn: (id: number) => api.watchlist.unpin(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });

  const grouped = (data ?? []).reduce<Record<string, WatchlistEntry[]>>((acc, w) => {
    (acc[w.entity_type] ||= []).push(w);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Watchlist</h1>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          Pinned creators, tickers, and individual calls. Click ★ on any of those pages to add.
        </p>
      </div>

      {isLoading && <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>}
      {error && <div className="text-sm text-[var(--negative)]">API not reachable.</div>}

      {data && data.length === 0 && (
        <Card>
          <p className="text-sm text-[var(--muted-foreground)]">
            No items pinned yet. The ★ button on call, creator, and ticker pages adds to this list.
          </p>
        </Card>
      )}

      {(["call", "creator", "ticker"] as const).map((kind) => {
        const items = grouped[kind] || [];
        if (!items.length) return null;
        return (
          <Card key={kind}>
            <CardHeader title={kind.charAt(0).toUpperCase() + kind.slice(1) + "s"} subtitle={`${items.length} pinned`} />
            <ul className="divide-y divide-[var(--border)] -my-2">
              {items.map((w) => (
                <li key={w.id} className="py-3 flex items-center justify-between text-sm">
                  <Link
                    href={
                      kind === "call"
                        ? `/calls/${w.entity_id}`
                        : kind === "creator"
                          ? `/creators/${w.entity_id}`
                          : `/tickers/${w.entity_id}`
                    }
                    className="font-medium hover:underline"
                  >
                    {kind === "ticker" ? <span className="font-mono">{w.entity_id}</span> : `${kind} #${w.entity_id}`}
                  </Link>
                  <div className="flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
                    <span>pinned {fmtDate(w.pinned_at)}</span>
                    <button
                      onClick={() => unpin.mutate(w.id)}
                      className="hover:text-[var(--negative)]"
                    >
                      Unpin
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}
