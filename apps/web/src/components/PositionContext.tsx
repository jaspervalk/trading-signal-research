"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type PositionView } from "@/lib/api";
import { cn, fmtPct, pctColor } from "@/lib/utils";

/**
 * What the owner holds in this ticker, shown on the research page.
 *
 * Researching a name you already own is a different question from researching
 * a fresh one ("should I trim" vs "should I buy"), and the page previously gave
 * no indication which situation you were in. Everything here is arithmetic over
 * the trade ledger and a delayed quote: no judgement, no advice, no sizing.
 */
export function PositionContext({
  ticker,
  invalidation,
}: {
  ticker: string;
  /** Deterministic invalidation level from the research view, when available. */
  invalidation?: number | null;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api.portfolio.get(true),
    staleTime: 60_000,
  });

  if (isLoading || !data) return null;

  const position = data.open_positions.find((p) => p.ticker === ticker);
  if (!position) return <NotHeld ticker={ticker} />;

  const weight =
    position.market_value_eur !== null && data.total_market_value_eur
      ? position.market_value_eur / data.total_market_value_eur
      : null;

  return (
    <section className="bg-[var(--panel)] border border-[var(--info)]/40 font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)] flex items-baseline justify-between gap-3">
        <h2 className="text-sm tracking-tight text-[var(--info)]">You hold this</h2>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
          computed · {position.trade_count} trade{position.trade_count === 1 ? "" : "s"}
        </span>
      </header>

      <div className="px-4 py-3 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          {/* Forced en-US: the browser locale would render "1,21" beside a
              "$328.29" two lines down, mixing decimal separators in one card. */}
          <span className="text-2xl font-medium tabular-nums">
            {position.quantity.toLocaleString("en-US", { maximumFractionDigits: 4 })}
          </span>
          <span className="text-sm text-[var(--muted-foreground)]">shares</span>
          {position.unrealized_pct !== null && (
            <span
              className={cn(
                "text-sm tabular-nums ml-auto",
                pctColor(position.unrealized_pct / 100),
              )}
            >
              {position.unrealized_pnl !== null && (
                <>
                  {position.unrealized_pnl > 0 ? "+" : ""}
                  {position.unrealized_pnl.toFixed(2)} {position.currency}{" "}
                </>
              )}
              {fmtPct(position.unrealized_pct / 100)}
            </span>
          )}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
          <Row label="avg cost" value={money(position.avg_cost, position.currency)} />
          <Row label="last" value={money(position.last_price, position.currency)} />
          <Row label="cost basis" value={money(position.cost_basis, position.currency)} />
          <Row label="market value" value={money(position.market_value, position.currency)} />
          {position.eur_avg_cost !== null && (
            <Row label="avg cost eur" value={money(position.eur_avg_cost, "EUR")} />
          )}
          {position.market_value_eur !== null && (
            <Row label="value eur" value={money(position.market_value_eur, "EUR")} />
          )}
          {weight !== null && (
            // A weight is a share of a whole, not a change, so no signed prefix.
            <Row label="portfolio weight" value={`${(weight * 100).toFixed(2)}%`} />
          )}
        </dl>

        <InvalidationReadout position={position} invalidation={invalidation ?? null} />
      </div>
    </section>
  );
}

/**
 * Where the research view's invalidation level sits relative to what was paid.
 * A stop below cost means exiting there realises a loss; the size of that gap
 * is the number worth knowing before adding.
 */
function InvalidationReadout({
  position,
  invalidation,
}: {
  position: PositionView;
  invalidation: number | null;
}) {
  if (invalidation === null || position.avg_cost === null) return null;

  const vsCost = (invalidation - position.avg_cost) / position.avg_cost;
  const atRisk =
    position.last_price !== null
      ? (position.last_price - invalidation) * position.quantity
      : null;
  const below = invalidation < position.avg_cost;

  return (
    <div className="pt-2.5 border-t border-[var(--hairline-2)] space-y-1">
      <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
        Against your cost
      </p>
      <p className="text-[12px] leading-relaxed">
        Invalidation {money(invalidation, position.currency)} is{" "}
        <span className={below ? "text-[var(--negative)]" : "text-[var(--positive)]"}>
          {fmtPct(Math.abs(vsCost))} {below ? "below" : "above"}
        </span>{" "}
        your average cost.
        {atRisk !== null && (
          <>
            {" "}
            Exiting there from today&apos;s price moves{" "}
            <span className="tabular-nums">
              {atRisk.toFixed(2)} {position.currency}
            </span>
            .
          </>
        )}
      </p>
    </div>
  );
}

function NotHeld({ ticker }: { ticker: string }) {
  return (
    <section className="bg-[var(--panel-2)] border border-[var(--border)] px-4 py-3 font-mono-jb">
      <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
        Not in your portfolio
      </p>
      <p className="text-[12px] text-[var(--muted-2)] mt-1">
        No recorded position in {ticker}. Log one from the portfolio page after you trade.
      </p>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-[var(--muted-foreground)] uppercase tracking-wider">{label}</dt>
      <dd className="text-right tabular-nums">{value}</dd>
    </>
  );
}

function money(value: number | null, currency: string): string {
  if (value === null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}
