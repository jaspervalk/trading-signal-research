"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Card } from "@/components/Card";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

export default function TickersIndexPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["tickers"],
    queryFn: () => api.tickers.list(),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Tickers</h1>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          Every ticker that has appeared in an extracted call.
        </p>
      </div>

      <Card>
        {isLoading && <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>}
        {data && data.length === 0 && (
          <p className="text-sm text-[var(--muted-foreground)]">No tickers yet — run the extractor.</p>
        )}
        {data && data.length > 0 && (
          <table className="w-full text-sm num">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                <th className="py-2">Ticker</th>
                <th className="py-2 text-right">Calls</th>
                <th className="py-2 text-right">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {data.map((t) => (
                <tr key={t.ticker} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--muted)]">
                  <td className="py-2.5">
                    <Link href={`/tickers/${t.ticker}`} className="font-mono font-medium hover:underline">
                      {t.ticker}
                    </Link>
                  </td>
                  <td className="py-2.5 text-right">{t.n_calls}</td>
                  <td className="py-2.5 text-right text-[var(--muted-foreground)]">{fmtDate(t.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
