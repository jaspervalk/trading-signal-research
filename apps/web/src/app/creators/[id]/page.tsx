"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { Card, CardHeader, Badge } from "@/components/Card";
import { api } from "@/lib/api";
import { cn, fmtDate, fmtPct, pctColor, statusColor } from "@/lib/utils";

export default function CreatorDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const creatorId = parseInt(id, 10);

  const { data: creator } = useQuery({
    queryKey: ["creator", creatorId],
    queryFn: () => api.creators.get(creatorId),
  });

  const { data: scorecards } = useQuery({
    queryKey: ["scorecards", creatorId],
    queryFn: () => api.leaderboard.scorecards(creatorId),
  });

  const { data: callsPage } = useQuery({
    queryKey: ["calls", { creator_id: creatorId, limit: 50 }],
    queryFn: () => api.calls.list({ creator_id: creatorId, limit: 50 }),
  });

  if (!creator) return <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-[var(--muted-foreground)] hover:underline">← Leaderboard</Link>
        <h1 className="text-2xl font-semibold tracking-tight mt-1">{creator.display_name}</h1>
        {creator.notes && (
          <p className="text-sm text-[var(--muted-foreground)] mt-2 max-w-3xl whitespace-pre-line">{creator.notes}</p>
        )}
      </div>

      <Card>
        <CardHeader title="Scorecards across windows + horizons" />
        {!scorecards || scorecards.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">No scorecards yet — run <code>tsr score</code>.</p>
        ) : (
          <div className="overflow-x-auto -mx-6">
            <table className="w-full text-sm num">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                  <th className="py-2 px-4">Window</th>
                  <th className="py-2 px-4">Horizon</th>
                  <th className="py-2 px-4 text-right">N</th>
                  <th className="py-2 px-4 text-right">Activated</th>
                  <th className="py-2 px-4 text-right">Hit rate</th>
                  <th className="py-2 px-4 text-right">Mean return</th>
                  <th className="py-2 px-4 text-right">Excess vs SPY</th>
                  <th className="py-2 px-4 text-right">Sharpe-like</th>
                </tr>
              </thead>
              <tbody>
                {scorecards.map((sc) => (
                  <tr key={sc.id} className="border-b border-[var(--border)] last:border-0">
                    <td className="py-2 px-4">{sc.window_label}</td>
                    <td className="py-2 px-4 font-medium">{sc.horizon}</td>
                    <td className="py-2 px-4 text-right">{sc.n_calls}</td>
                    <td className="py-2 px-4 text-right">{sc.n_activated}</td>
                    <td className="py-2 px-4 text-right">
                      {sc.hit_rate !== null
                        ? `${(sc.hit_rate * 100).toFixed(0)}% [${(sc.hit_rate_lower_ci! * 100).toFixed(0)}-${(sc.hit_rate_upper_ci! * 100).toFixed(0)}]`
                        : "—"}
                    </td>
                    <td className={cn("py-2 px-4 text-right", pctColor(sc.mean_return))}>{fmtPct(sc.mean_return)}</td>
                    <td className={cn("py-2 px-4 text-right font-medium", pctColor(sc.mean_excess_return))}>
                      {fmtPct(sc.mean_excess_return)}
                    </td>
                    <td className="py-2 px-4 text-right">{sc.sharpe_like?.toFixed(2) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title={`Recent calls (${callsPage?.total ?? 0})`} />
        {!callsPage || callsPage.items.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">No calls yet.</p>
        ) : (
          <div className="overflow-x-auto -mx-6">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                  <th className="py-2 px-4">Posted</th>
                  <th className="py-2 px-4">Ticker</th>
                  <th className="py-2 px-4">Direction</th>
                  <th className="py-2 px-4">Entry</th>
                  <th className="py-2 px-4">Status</th>
                  <th className="py-2 px-4 text-right">5d return</th>
                </tr>
              </thead>
              <tbody>
                {callsPage.items.map((it) => {
                  const c = it.call;
                  const five = it.outcomes.find((o) => o.horizon === "5d");
                  return (
                    <tr key={c.id} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--muted)]">
                      <td className="py-2 px-4 text-[var(--muted-foreground)] num">{fmtDate(it.posted_at)}</td>
                      <td className="py-2 px-4">
                        <Link href={`/calls/${c.id}`} className="font-medium font-mono hover:underline">{c.ticker}</Link>
                      </td>
                      <td className="py-2 px-4">{c.direction}</td>
                      <td className="py-2 px-4 text-[var(--muted-foreground)] text-xs">
                        {c.entry_type}{c.entry_price ? ` @ ${c.entry_price}` : ""}
                      </td>
                      <td className="py-2 px-4"><Badge className={statusColor(c.status)}>{c.status}</Badge></td>
                      <td className={cn("py-2 px-4 text-right num", pctColor(five?.return_pct))}>
                        {five ? fmtPct(five.return_pct) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
