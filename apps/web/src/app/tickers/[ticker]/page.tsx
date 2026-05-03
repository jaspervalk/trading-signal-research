"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { Card, CardHeader, Badge } from "@/components/Card";
import { PriceChart } from "@/components/PriceChart";
import { WatchlistButton } from "@/components/WatchlistButton";
import { api } from "@/lib/api";
import { cn, fmtDate, fmtPct, pctColor, statusColor } from "@/lib/utils";

export default function TickerDetailPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker: rawTicker } = use(params);
  const ticker = rawTicker.toUpperCase();

  const { data: summary } = useQuery({
    queryKey: ["ticker-summary", ticker],
    queryFn: () => api.tickers.get(ticker),
  });
  const { data: bars } = useQuery({
    queryKey: ["ticker-bars", ticker],
    queryFn: () => api.tickers.bars(ticker, 365),
  });
  const { data: calls } = useQuery({
    queryKey: ["ticker-calls", ticker],
    queryFn: () => api.tickers.calls(ticker),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/tickers" className="text-sm text-[var(--muted-foreground)] hover:underline">← Tickers</Link>
          <h1 className="text-3xl font-semibold tracking-tight font-mono mt-1">{ticker}</h1>
          {summary && (
            <p className="text-sm text-[var(--muted-foreground)] mt-1 num">
              {summary.n_calls} call{summary.n_calls === 1 ? "" : "s"} ·{" "}
              {Object.entries(summary.by_direction).map(([d, n]) => `${n} ${d}`).join(" · ") || "—"}
            </p>
          )}
        </div>
        <WatchlistButton entityType="ticker" entityId={ticker} />
      </div>

      <Card>
        <CardHeader title="Price (last 365 days)" subtitle="Markers show the moment of each extracted call. Colour = 5d return after the call (green positive, red negative, grey not yet evaluated)." />
        {!bars || bars.bars.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">
            No price data — yfinance returned nothing for this ticker.
          </p>
        ) : (
          <PriceChart bars={bars.bars} calls={calls ?? []} />
        )}
      </Card>

      <Card>
        <CardHeader title={`All calls on ${ticker} (${calls?.length ?? 0})`} />
        {!calls || calls.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">No calls yet.</p>
        ) : (
          <div className="overflow-x-auto -mx-6">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                  <th className="py-2 px-4">Posted</th>
                  <th className="py-2 px-4">Creator</th>
                  <th className="py-2 px-4">Direction</th>
                  <th className="py-2 px-4">Entry</th>
                  <th className="py-2 px-4 text-right">Conf</th>
                  <th className="py-2 px-4">Status</th>
                  <th className="py-2 px-4 text-right">5d return</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => (
                  <tr key={c.call_id} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--muted)]">
                    <td className="py-2.5 px-4 num text-[var(--muted-foreground)]">{fmtDate(c.posted_at)}</td>
                    <td className="py-2.5 px-4">
                      <Link href={`/creators/${c.creator_id}`} className="hover:underline">{c.creator_name}</Link>
                    </td>
                    <td className="py-2.5 px-4">{c.direction}</td>
                    <td className="py-2.5 px-4 text-[var(--muted-foreground)] text-xs">
                      {c.entry_type}{c.entry_price ? ` @ ${c.entry_price}` : ""}
                    </td>
                    <td className="py-2.5 px-4 text-right num">{c.final_confidence.toFixed(2)}</td>
                    <td className="py-2.5 px-4">
                      <Link href={`/calls/${c.call_id}`}>
                        <Badge className={statusColor(c.status)}>{c.status}</Badge>
                      </Link>
                    </td>
                    <td className={cn("py-2.5 px-4 text-right num", pctColor(c.return_5d))}>
                      {c.return_5d !== null ? fmtPct(c.return_5d) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
