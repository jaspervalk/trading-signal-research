"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/Card";
import { PolarityBar } from "@/components/PolarityBar";
import { api } from "@/lib/api";
import { cn, fmtIsoDate, fmtSigned } from "@/lib/utils";

/**
 * IA section 6: per-creator coverage rollup for this ticker. Sorted server-side:
 * calibrated creators first, then by hit_rate_lower_ci desc, then by mention count.
 *
 * Each row carries bracketed metadata `[N_CALLS=…] [N_CLAIMS=…] [HR_LCI=…]` per
 * the composite mockup's signature pattern.
 */
export function CreatorCoverage({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ticker-coverage", ticker],
    queryFn: () => api.tickers.coverage(ticker),
  });

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)]">
      <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm font-mono-jb tracking-tight">
          Creator coverage · {data?.length ?? 0}
        </h2>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] font-mono-jb">
          credibility-sorted
        </span>
      </header>
      {isLoading && (
        <p className="px-4 py-3 text-xs text-[var(--muted-foreground)] font-mono-jb">
          Loading coverage…
        </p>
      )}
      {error && (
        <p className="px-4 py-3 text-xs text-[var(--negative)] font-mono-jb">
          Coverage API error
        </p>
      )}
      {!isLoading && data && data.length === 0 && (
        <p className="px-4 py-3 text-xs text-[var(--muted-foreground)] font-mono-jb">
          No tracked creator has mentioned this ticker.
        </p>
      )}
      <ul>
        {(data ?? []).map((row) => (
          <li
            key={row.creator_id}
            className="border-b border-[var(--hairline-2)] last:border-b-0 px-4 py-3 font-mono-jb"
          >
            <div className="flex items-center justify-between gap-3 text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
              <span>
                [N_CALLS={row.n_calls}] [N_CLAIMS={row.n_claims}]{" "}
                {row.is_calibrated && row.hit_rate_lower_ci !== null
                  ? `[HR_LCI=${row.hit_rate_lower_ci.toFixed(2)}]`
                  : "[UNCALIBRATED]"}
              </span>
              <span className="text-[var(--muted-2)]">
                {fmtIsoDate(row.most_recent_mention_at)}
              </span>
            </div>
            <div className="mt-1 flex items-center justify-between gap-3">
              <Link
                href={`/creators/${row.creator_id}`}
                className="text-sm hover:text-[var(--info)] truncate"
              >
                {row.creator_name}
              </Link>
              <span
                className={cn(
                  "text-xs",
                  row.net_polarity_on_this_ticker !== null
                    ? row.net_polarity_on_this_ticker > 0.001
                      ? "text-[var(--positive)]"
                      : row.net_polarity_on_this_ticker < -0.001
                        ? "text-[var(--negative)]"
                        : "text-[var(--muted-foreground)]"
                    : "text-[var(--muted-foreground)]",
                )}
              >
                {fmtSigned(row.net_polarity_on_this_ticker)}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-3">
              <PolarityBar value={row.net_polarity_on_this_ticker} className="flex-1" />
              {!row.is_calibrated && (
                <Badge className="text-[var(--warning)] border-[color:rgba(245,158,11,0.4)] bg-[color:rgba(245,158,11,0.06)] uppercase tracking-wider">
                  uncalibrated · N&lt;5
                </Badge>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
