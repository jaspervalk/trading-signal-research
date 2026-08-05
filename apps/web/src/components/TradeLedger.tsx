"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type PortfolioTrade } from "@/lib/api";

export function TradeLedger() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["portfolio-trades"],
    queryFn: () => api.portfolio.trades(),
  });

  const del = useMutation({
    mutationFn: (id: number) => api.portfolio.deleteTrade(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["portfolio-trades"] });
    },
  });

  if (isLoading) return <p className="text-sm text-[var(--muted-foreground)]">Loading trades…</p>;
  if (!data || data.length === 0)
    return <p className="text-sm text-[var(--muted-foreground)]">No trades recorded yet.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-[var(--muted-foreground)]">
          <tr>
            <th className="py-2">Date</th>
            <th className="py-2">Ticker</th>
            <th className="py-2">Side</th>
            <th className="py-2 text-right">Qty</th>
            <th className="py-2 text-right">Price</th>
            <th className="py-2 text-right">EUR</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {data.map((t: PortfolioTrade) => (
            <tr key={t.id} className="border-t border-[var(--border)]">
              <td className="py-2">{t.traded_at.slice(0, 10)}</td>
              <td className="py-2 font-medium">{t.ticker}</td>
              <td className="py-2">{t.side}</td>
              <td className="py-2 text-right tabular-nums">{t.quantity}</td>
              <td className="py-2 text-right tabular-nums">
                {t.price_per_share.toFixed(2)} {t.currency}
              </td>
              <td className="py-2 text-right tabular-nums">
                {t.eur_amount !== null ? t.eur_amount.toFixed(2) : "—"}
              </td>
              <td className="py-2 text-right">
                <button
                  onClick={() => del.mutate(t.id)}
                  className="text-xs text-[var(--muted-foreground)] hover:text-red-500"
                >
                  delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
