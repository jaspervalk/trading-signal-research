"use client";

import { useQuery } from "@tanstack/react-query";

import { ClaimRow } from "@/components/ClaimRow";
import { api } from "@/lib/api";

/**
 * IA section 4: recent claims feed. Defaults to status='accepted', most-recent
 * first (the API default). Filters (claim_type, claim_class, polarity, creator,
 * time window) deferred to a follow-up per the user's V1 scope decision.
 */
export function ClaimsFeed({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticker-claims", ticker],
    queryFn: () => api.tickers.claims(ticker, { limit: 50 }),
  });

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)]">
      <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm font-mono-jb tracking-tight">
          Recent claims · {data?.length ?? 0}
        </h2>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] font-mono-jb">
          status: accepted · sorted: most recent
        </span>
      </header>
      {isLoading && (
        <p className="px-4 py-3 text-xs text-[var(--muted-foreground)] font-mono-jb">
          Loading claims…
        </p>
      )}
      {error && (
        <p className="px-4 py-3 text-xs text-[var(--negative)] font-mono-jb">
          Claims API error · check uvicorn logs
        </p>
      )}
      {!isLoading && data && data.length === 0 && (
        <p className="px-4 py-3 text-xs text-[var(--muted-foreground)] font-mono-jb">
          No accepted claims for this ticker yet.
        </p>
      )}
      <div>{(data ?? []).map((c) => <ClaimRow key={c.claim_id} claim={c} />)}</div>
    </section>
  );
}
