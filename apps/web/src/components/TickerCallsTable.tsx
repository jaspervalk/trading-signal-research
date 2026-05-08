"use client";

import Link from "next/link";

import { Badge } from "@/components/Card";
import type { TickerCall } from "@/lib/api";
import { cn, fmtDate, fmtPct, pctColor, statusColor } from "@/lib/utils";

/**
 * IA section 5: structured trade calls (entry/target/stop) and resolved outcomes.
 * Shown below the claims feed since claims are the higher-volume surface per IA.
 */
export function TickerCallsTable({ calls }: { calls: TickerCall[] }) {
  return (
    <section className="bg-[var(--panel)] border border-[var(--border)]">
      <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm font-mono-jb tracking-tight">
          Trade calls · {calls.length}
        </h2>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] font-mono-jb">
          structured · entry / target / stop
        </span>
      </header>
      {calls.length === 0 ? (
        <p className="px-4 py-3 text-xs text-[var(--muted-foreground)] font-mono-jb">
          No structured calls on this ticker yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono-jb">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] border-b border-[var(--hairline-2)]">
                <th className="py-2 px-3">Posted</th>
                <th className="py-2 px-3">Creator</th>
                <th className="py-2 px-3">Dir</th>
                <th className="py-2 px-3">Entry</th>
                <th className="py-2 px-3 text-right">Conf</th>
                <th className="py-2 px-3">Status</th>
                <th className="py-2 px-3 text-right">5d</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr
                  key={c.call_id}
                  className="border-b border-[var(--hairline-2)] last:border-b-0 hover:bg-[color:rgba(255,255,255,0.02)]"
                >
                  <td className="py-2 px-3 text-[var(--muted-foreground)]">
                    {fmtDate(c.posted_at)}
                  </td>
                  <td className="py-2 px-3 truncate max-w-[140px]">
                    <Link
                      href={`/creators/${c.creator_id}`}
                      className="hover:text-[var(--info)]"
                    >
                      {c.creator_name}
                    </Link>
                  </td>
                  <td className="py-2 px-3">{c.direction}</td>
                  <td className="py-2 px-3 text-[var(--muted-foreground)]">
                    {c.entry_type}
                    {c.entry_price !== null ? ` @ ${c.entry_price}` : ""}
                  </td>
                  <td className="py-2 px-3 text-right">
                    {c.final_confidence.toFixed(2)}
                  </td>
                  <td className="py-2 px-3">
                    <Link href={`/calls/${c.call_id}`}>
                      <Badge className={cn("uppercase tracking-wider", statusColor(c.status))}>
                        {c.status}
                      </Badge>
                    </Link>
                  </td>
                  <td className={cn("py-2 px-3 text-right", pctColor(c.return_5d))}>
                    {c.return_5d !== null ? fmtPct(c.return_5d) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
