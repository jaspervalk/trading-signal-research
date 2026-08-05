"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { AddTradeForm } from "@/components/AddTradeForm";
import { Card, CardHeader } from "@/components/Card";
import { PortfolioTable } from "@/components/PortfolioTable";
import { TickerSearch } from "@/components/TickerSearch";
import { TradeLedger } from "@/components/TradeLedger";
import { api, ApiError } from "@/lib/api";

function money(value: number | null, currency = "USD") {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);
}

// Totals are null whenever they would be misleading — mixed currencies, an
// unpriced holding, or an unavailable FX rate. Render "—", never a partial sum.

export default function PortfolioPage() {
  const qc = useQueryClient();
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api.portfolio.get(true),
  });

  const totalsCurrency =
    data && data.open_positions.length > 0 ? data.open_positions[0].currency : "USD";

  return (
    <main className="mx-auto max-w-6xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Manually recorded trades, valued against delayed market quotes. Decision
          support only — you place every order yourself.
        </p>
      </div>

      <Card>
        <CardHeader title="Research a ticker" subtitle="Jump into technicals, setup, valuation and optional LLM research." />
        <TickerSearch />
      </Card>

      {error && (
        <Card>
          <p className="text-sm text-red-500">
            {error instanceof ApiError && error.message ? (
              error.message
            ) : (
              <>
                Could not reach the API. Start it with{" "}
                <code>uvicorn apps.api.app.main:app --reload --port 8001</code>.
              </>
            )}
          </p>
        </Card>
      )}

      <Card>
        <div className="mb-4 flex items-start justify-between gap-4">
          <CardHeader
            title="Holdings"
            subtitle={data ? `Priced ${new Date(data.as_of).toLocaleTimeString()} · quotes ~15 min delayed` : undefined}
          />
          <button
            onClick={() => qc.invalidateQueries({ queryKey: ["portfolio"] })}
            disabled={isFetching}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {isFetching ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {isLoading && <p className="text-sm text-[var(--muted-foreground)]">Loading…</p>}

        {data && (
          <>
            <div className="mb-6 grid gap-4 sm:grid-cols-4">
              <Stat label="Market value" value={money(data.total_market_value, totalsCurrency)} />
              <Stat label="Market value (EUR)" value={money(data.total_market_value_eur, "EUR")} />
              <Stat label="Unrealized P&L" value={money(data.total_unrealized_pnl, totalsCurrency)} />
              <Stat label="Realized P&L" value={money(data.total_realized_pnl, totalsCurrency)} />
            </div>
            <PortfolioTable positions={data.open_positions} />
            {data.quote_errors.length > 0 && (
              <p className="mt-3 text-xs text-[var(--muted-foreground)]">
                No quote available for: {data.quote_errors.join(", ")}
              </p>
            )}
          </>
        )}
      </Card>

      {data && data.closed_positions.length > 0 && (
        <Card>
          <CardHeader title="Closed positions" subtitle="Realized profit and loss." />
          <PortfolioTable positions={data.closed_positions} closed />
        </Card>
      )}

      <Card>
        <CardHeader title="Add a trade" subtitle="Price per share in the stock's own currency. EUR total is optional." />
        <AddTradeForm />
      </Card>

      <Card>
        <CardHeader title="Trade ledger" subtitle="Everything you have recorded." />
        <TradeLedger />
      </Card>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
