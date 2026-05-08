"use client";

import Link from "next/link";

import type { DecisionStatus, ScanRow } from "@/lib/api";
import { cn, fmtPct } from "@/lib/utils";

/**
 * Compact ranked status board — one row per ticker, sortable by quality.
 * Used by the watchlist page and (later) by ad-hoc scan results. Stays
 * inside the locked Terminal grammar: bg-[var(--panel)], hairline borders,
 * font-mono-jb, color-coded status.
 */
export function ScanStatusBoard({
  rows,
  emptyMessage = "No tickers to scan.",
}: {
  rows: ScanRow[];
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="text-xs text-[var(--muted-foreground)] font-mono-jb px-4 py-3 border border-[var(--border)] bg-[var(--panel)]">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto bg-[var(--panel)] border border-[var(--border)]">
      <table className="w-full font-mono-jb text-[11px]">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] border-b border-[var(--hairline-2)]">
            <Th>Ticker</Th>
            <Th>Status</Th>
            <Th>Setup</Th>
            <Th>Style</Th>
            <Th right>Last</Th>
            <Th right>5d</Th>
            <Th right>21d</Th>
            <Th right>RSI</Th>
            <Th right>ATR%</Th>
            <Th right>RS 63d</Th>
            <Th right>Pullback</Th>
            <Th right>Brk dist</Th>
            <Th right>R/R</Th>
            <Th>Tx</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <ScanRowEl key={r.ticker} row={r} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScanRowEl({ row }: { row: ScanRow }) {
  return (
    <tr className="border-b border-[var(--hairline-2)] last:border-b-0 hover:bg-[color:rgba(255,255,255,0.02)]">
      <Td>
        <Link
          href={`/tickers/${row.ticker}`}
          className="font-medium hover:text-[var(--info)]"
        >
          {row.ticker}
        </Link>
      </Td>
      <Td>
        <span className={cn("uppercase tracking-wider text-[11px]", statusText(row.status))}>
          {row.status.replace(/_/g, " ")}
          <span className="text-[var(--muted-foreground)] normal-case ml-1 tracking-normal">
            ({row.status_confidence[0].toUpperCase()})
          </span>
        </span>
      </Td>
      <Td>
        <span className="text-[var(--foreground)]">
          {row.setup_type.replace(/_/g, " ")}
        </span>
      </Td>
      <Td className="text-[var(--muted-foreground)]">
        {(row.primary_style ?? "—").replace(/_/g, " ")}
      </Td>
      <Td right>{fmtNum(row.last_close, 2)}</Td>
      <Td right className={signColor(row.return_5d)}>{fmtPct(row.return_5d, 1)}</Td>
      <Td right className={signColor(row.return_21d)}>{fmtPct(row.return_21d, 1)}</Td>
      <Td right className={rsiColor(row.rsi_14)}>{fmtNum(row.rsi_14, 0)}</Td>
      <Td right>{fmtPct(row.atr_14_pct, 1)}</Td>
      <Td right className={signColor(row.relative_strength_vs_spy_63d)}>
        {fmtPct(row.relative_strength_vs_spy_63d, 1)}
      </Td>
      <Td right>{fmtPct(row.pullback_pct_from_recent_high, 1)}</Td>
      <Td right className={signColor(row.breakout_distance_pct)}>
        {fmtPct(row.breakout_distance_pct, 1)}
      </Td>
      <Td right className={row.risk_reward_estimate ? "text-[var(--info)]" : ""}>
        {fmtNum(row.risk_reward_estimate, 1)}
      </Td>
      <Td>
        <TxIndicator row={row} />
      </Td>
    </tr>
  );
}

function TxIndicator({ row }: { row: ScanRow }) {
  const total = row.transcript_n_calls + row.transcript_n_claims;
  if (total === 0) {
    return <span className="text-[var(--muted-2)]">—</span>;
  }
  if (row.transcript_confirms === "confirms") {
    return <span className="text-[var(--positive)]" title="Transcript confirms setup">✓</span>;
  }
  if (row.transcript_confirms === "contradicts") {
    return <span className="text-[var(--negative)]" title="Transcript contradicts setup">✗</span>;
  }
  return <span className="text-[var(--muted-foreground)]" title="Transcript present but irrelevant">·</span>;
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cn("py-2 px-2.5 whitespace-nowrap", right && "text-right")}>{children}</th>;
}

function Td({
  children,
  right,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  className?: string;
}) {
  return (
    <td className={cn("py-2 px-2.5 whitespace-nowrap tabular-nums", right && "text-right", className)}>
      {children}
    </td>
  );
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
}

function statusText(s: DecisionStatus): string {
  switch (s) {
    case "research_candidate":
      return "text-[var(--positive)]";
    case "watch":
      return "text-[var(--info)]";
    case "wait_for_setup":
      return "text-[var(--muted-foreground)]";
    case "skip_for_now":
      return "text-[var(--negative)]";
    case "extended_risk":
      return "text-[var(--warning)]";
    case "insufficient_data":
      return "text-[var(--muted-2)]";
  }
}

function signColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (v > 0.0005) return "text-[var(--positive)]";
  if (v < -0.0005) return "text-[var(--negative)]";
  return "";
}

function rsiColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (v >= 70) return "text-[var(--warning)]";
  if (v <= 30) return "text-[var(--info)]";
  return "";
}
