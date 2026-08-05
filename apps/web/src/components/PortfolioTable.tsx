"use client";

import Link from "next/link";
import type { PositionView } from "@/lib/api";

function money(value: number | null, currency = "USD") {
  if (value === null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function pct(value: number | null) {
  if (value === null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function toneFor(value: number | null) {
  if (value === null) return "text-[var(--muted-foreground)]";
  return value >= 0 ? "text-emerald-500" : "text-red-500";
}

export function PortfolioTable({
  positions,
  closed = false,
}: {
  positions: PositionView[];
  closed?: boolean;
}) {
  if (positions.length === 0) {
    return (
      <p className="text-sm text-[var(--muted-foreground)]">
        {closed ? "No closed positions yet." : "No open positions. Add a trade below."}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-[var(--muted-foreground)]">
          <tr>
            <th className="py-2">Ticker</th>
            <th className="py-2 text-right">Qty</th>
            <th className="py-2 text-right">Avg cost</th>
            {!closed && <th className="py-2 text-right">Last</th>}
            {!closed && <th className="py-2 text-right">Value</th>}
            {!closed && <th className="py-2 text-right">Unrealized</th>}
            {!closed && <th className="py-2 text-right">Day</th>}
            <th className="py-2 text-right">Realized</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticker} className="border-t border-[var(--border)]">
              <td className="py-2 font-medium">
                <Link href={`/tickers/${p.ticker}`} className="hover:underline">
                  {p.ticker}
                </Link>
              </td>
              {/* Forced en-US: the browser locale renders "1,21" next to a
                  "$328.29" in the same row, mixing decimal separators. */}
              <td className="py-2 text-right tabular-nums">
                {p.quantity.toLocaleString("en-US", { maximumFractionDigits: 4 })}
              </td>
              <td className="py-2 text-right tabular-nums">{money(p.avg_cost, p.currency)}</td>
              {!closed && (
                <td className="py-2 text-right tabular-nums">{money(p.last_price, p.currency)}</td>
              )}
              {!closed && (
                <td className="py-2 text-right tabular-nums">
                  {money(p.market_value, p.currency)}
                  {p.market_value_eur !== null && (
                    <span className="block text-xs text-[var(--muted-foreground)]">
                      {money(p.market_value_eur, "EUR")}
                    </span>
                  )}
                </td>
              )}
              {!closed && (
                <td className={`py-2 text-right tabular-nums ${toneFor(p.unrealized_pnl)}`}>
                  {money(p.unrealized_pnl, p.currency)}
                  <span className="block text-xs">{pct(p.unrealized_pct)}</span>
                </td>
              )}
              {!closed && (
                <td className={`py-2 text-right tabular-nums ${toneFor(p.day_change_pct)}`}>
                  {pct(p.day_change_pct)}
                </td>
              )}
              <td className={`py-2 text-right tabular-nums ${toneFor(p.realized_pnl)}`}>
                {money(p.realized_pnl, p.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
