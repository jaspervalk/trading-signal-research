"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Card, CardHeader, Badge } from "@/components/Card";
import { api } from "@/lib/api";
import { cn, fmtPct, pctColor } from "@/lib/utils";

const HORIZONS = ["1d", "3d", "5d", "21d"];
const WINDOWS = ["all", "365d", "90d"];

export default function LeaderboardPage() {
  const [horizon, setHorizon] = useState("5d");
  const [windowLabel, setWindowLabel] = useState("all");

  const { data: rows, isLoading, error } = useQuery({
    queryKey: ["leaderboard", horizon, windowLabel],
    queryFn: () => api.leaderboard.get(horizon, windowLabel),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Creators</h1>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          Creator accuracy scorecards — an input to research, not the product.
        </p>
      </div>

      <Card>
        <div className="flex items-center gap-6 mb-6">
          <Filter label="Horizon" value={horizon} onChange={setHorizon} options={HORIZONS} />
          <Filter label="Window" value={windowLabel} onChange={setWindowLabel} options={WINDOWS} />
        </div>

        {isLoading && <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>}
        {error && (
          <div className="text-sm text-[var(--negative)]">
            API not reachable. Run <code className="font-mono">uvicorn apps.api.app.main:app --port 8001</code>.
          </div>
        )}
        {rows && rows.length === 0 && (
          <div className="text-sm text-[var(--muted-foreground)]">
            No scorecards at this horizon/window yet. Run <code>tsr score</code>.
          </div>
        )}

        {rows && rows.length > 0 && (
          <div className="overflow-x-auto -mx-6">
            <table className="w-full text-sm num">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                  <Th>Creator</Th>
                  <Th right>N calls</Th>
                  <Th right>Activated</Th>
                  <Th right>Tickers</Th>
                  <Th right>Hit rate (95% CI)</Th>
                  <Th right>Mean return</Th>
                  <Th right>Excess vs SPY</Th>
                  <Th right>Sharpe-like</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const lowN = r.n_activated < 5;
                  return (
                    <tr
                      key={r.creator_id}
                      className={cn(
                        "border-b border-[var(--border)] last:border-0 hover:bg-[var(--muted)] transition-colors",
                        lowN && "opacity-50",
                      )}
                    >
                      <Td>
                        <Link
                          href={`/creators/${r.creator_id}`}
                          className="font-medium hover:underline"
                        >
                          {r.creator_name}
                        </Link>
                        {lowN && (
                          <Badge className="ml-2 bg-[var(--warning)]/10 text-[var(--warning)] border-[var(--warning)]/20">
                            low N
                          </Badge>
                        )}
                      </Td>
                      <Td right>{r.n_calls}</Td>
                      <Td right>{r.n_activated}</Td>
                      <Td right>{r.n_unique_tickers}</Td>
                      <Td right>
                        {r.hit_rate !== null ? (
                          <>
                            {(r.hit_rate * 100).toFixed(0)}%{" "}
                            <span className="text-[var(--muted-foreground)] text-xs">
                              [{(r.hit_rate_lower_ci! * 100).toFixed(0)}-{(r.hit_rate_upper_ci! * 100).toFixed(0)}]
                            </span>
                          </>
                        ) : (
                          "—"
                        )}
                      </Td>
                      <Td right className={pctColor(r.mean_return)}>{fmtPct(r.mean_return)}</Td>
                      <Td right className={cn("font-medium", pctColor(r.mean_excess_return))}>
                        {fmtPct(r.mean_excess_return)}
                      </Td>
                      <Td right>{r.sharpe_like !== null ? r.sharpe_like.toFixed(2) : "—"}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="Reading guide" />
        <div className="text-sm text-[var(--muted-foreground)] space-y-2">
          <p>
            <span className="font-medium text-[var(--foreground)]">Excess vs SPY</span> is the headline number — creator return minus SPY return over the same window. Positive means the creator added something beyond riding the market.
          </p>
          <p>
            <span className="font-medium text-[var(--foreground)]">Hit rate Wilson CI</span> tells you how confident we can be in the rate. If the lower bound is below 50%, the creator does not statistically beat a coin flip yet.
          </p>
          <p>
            <span className="font-medium text-[var(--foreground)]">Low N badge</span> appears below 5 activated calls. Take everything those rows say with significant skepticism.
          </p>
        </div>
      </Card>
    </div>
  );
}

function Filter({
  label, value, onChange, options,
}: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <div className="flex gap-1 rounded-md border border-[var(--border)] p-0.5">
        {options.map((opt) => (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition-colors",
              value === opt
                ? "bg-[var(--muted)] text-[var(--foreground)]"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
            )}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={cn("py-2 px-4", right && "text-right")}>{children}</th>
  );
}

function Td({
  children, right, className,
}: { children: React.ReactNode; right?: boolean; className?: string }) {
  return (
    <td className={cn("py-3 px-4", right && "text-right", className)}>{children}</td>
  );
}
