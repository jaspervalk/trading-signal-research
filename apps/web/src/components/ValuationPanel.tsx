import { Provenance } from "@/components/Provenance";
import type { ResearchValuation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Age of the metadata, in the coarsest unit that is still honest. */
function freshness(fetchedAt: string | null): string | undefined {
  if (!fetchedAt) return undefined;
  const ms = Date.now() - new Date(fetchedAt).getTime();
  if (Number.isNaN(ms) || ms < 0) return undefined;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m old`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h old`;
  return `${Math.floor(hours / 24)}d old`;
}

/**
 * Valuation context ribbon. Decision-support context, never a trigger.
 *
 * Per the Q3 R/R-methodology answer: valuation does NOT feed into R/R math.
 * It surfaces here as a separate axis so the user can weigh "rich/cheap"
 * alongside the trade setup without conflating them.
 *
 * Empty state (e.g. ETF with no fwd P/E) renders a one-line note rather
 * than a half-empty grid.
 */
export function ValuationPanel({ valuation }: { valuation: ResearchValuation }) {
  if (_isEmpty(valuation)) {
    return (
      <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb p-4 text-xs text-[var(--muted-foreground)]">
        <h2 className="text-sm tracking-tight text-[var(--foreground)] mb-1">
          Valuation
        </h2>
        <p>
          No valuation data — likely an ETF, sector fund, or pre-IPO ticker.
        </p>
      </section>
    );
  }

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-sm tracking-tight">Valuation</h2>
          {/* Multiples move with price, so their age is load-bearing: a stale
              forward P/E reads as a live one unless the fetch time is shown. */}
          <Provenance kind="computed" detail={freshness(valuation.fetched_at)} />
        </div>
        <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          {valuation.sector || "—"} · {valuation.industry || "—"} · context, not trigger
        </p>
      </header>
      <div className="p-4 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <Field label="forward P/E" value={fmtRatio(valuation.forward_pe)} hint={peHint(valuation.forward_pe)} />
        <Field label="trailing P/E" value={fmtRatio(valuation.trailing_pe)} />
        <Field label="PEG" value={fmtRatio(valuation.peg_ratio, 2)} hint={pegHint(valuation.peg_ratio)} />
        <Field label="P/S TTM" value={fmtRatio(valuation.price_to_sales_ttm)} />
        <Field label="EV/EBITDA" value={fmtRatio(valuation.enterprise_to_ebitda)} />
        <Field label="market cap" value={fmtUsd(valuation.market_cap)} />
        <Field
          label="rev growth YoY"
          value={fmtPct(valuation.revenue_growth_yoy)}
          color={signColor(valuation.revenue_growth_yoy)}
        />
        <Field
          label="EPS growth fwd"
          value={fmtPct(valuation.earnings_growth_forward)}
          color={signColor(valuation.earnings_growth_forward)}
        />
        <Field
          label="profit margin"
          value={fmtPct(valuation.profit_margins)}
          color={signColor(valuation.profit_margins)}
        />
        <Field label="float" value={fmtUsd(valuation.float_shares, true)} />
        <Field
          label="short % float"
          value={fmtPct(valuation.short_pct_of_float)}
          color={shortHint(valuation.short_pct_of_float)}
        />
        <Field label="beta" value={fmtRatio(valuation.beta, 2)} />
        <Field label="div yield" value={fmtDivYield(valuation.dividend_yield)} />
        <Field
          label="days to earnings"
          value={fmtEarnings(valuation.days_to_next_earnings)}
          color={earningsHint(valuation.days_to_next_earnings)}
        />
      </div>
    </section>
  );
}

function _isEmpty(v: ResearchValuation): boolean {
  // Treat as empty when none of the headline valuation slots have data.
  return (
    v.forward_pe === null &&
    v.trailing_pe === null &&
    v.market_cap === null &&
    v.price_to_sales_ttm === null
  );
}

function Field({
  label,
  value,
  color,
  hint,
}: {
  label: string;
  value: string;
  color?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--muted-foreground)]">
        {label}
        {hint && <span className="ml-1 text-[var(--muted-2)]">[{hint}]</span>}
      </span>
      <span className={cn("tabular-nums", color)}>{value}</span>
    </div>
  );
}

function fmtRatio(v: number | null, digits = 1): string {
  if (v === null) return "—";
  return v.toFixed(digits);
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDivYield(v: number | null): string {
  // yfinance returns `dividendYield` already in percent form (0.38 = 0.38%),
  // unlike `revenueGrowth` / `shortPercentOfFloat` which are fractions. Don't
  // multiply by 100 here. If a future yfinance version returns it as a fraction
  // (< 0.05 typical max ~ 8%), assume fraction-form and renormalise.
  if (v === null) return "—";
  const pct = v < 0.05 ? v * 100 : v;
  return `${pct.toFixed(2)}%`;
}

function fmtUsd(v: number | null, sharesNotDollars = false): string {
  if (v === null) return "—";
  const prefix = sharesNotDollars ? "" : "$";
  if (Math.abs(v) >= 1e12) return `${prefix}${(v / 1e12).toFixed(1)}T`;
  if (Math.abs(v) >= 1e9) return `${prefix}${(v / 1e9).toFixed(1)}B`;
  if (Math.abs(v) >= 1e6) return `${prefix}${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `${prefix}${(v / 1e3).toFixed(0)}K`;
  return `${prefix}${v.toFixed(0)}`;
}

function fmtEarnings(days: number | null): string {
  if (days === null) return "—";
  if (days < 0) return `${Math.abs(days)}d post`;
  if (days === 0) return "today";
  return `${days}d`;
}

function signColor(v: number | null): string | undefined {
  if (v === null) return undefined;
  if (v > 0.005) return "text-[var(--positive)]";
  if (v < -0.005) return "text-[var(--negative)]";
  return undefined;
}

function peHint(pe: number | null): string | undefined {
  if (pe === null) return undefined;
  if (pe < 0) return "loss";
  if (pe < 15) return "low";
  if (pe < 25) return "mid";
  if (pe < 40) return "rich";
  return "very rich";
}

function pegHint(peg: number | null): string | undefined {
  if (peg === null) return undefined;
  if (peg <= 0) return undefined;
  if (peg < 1) return "cheap vs growth";
  if (peg < 2) return "fair";
  return "rich vs growth";
}

function shortHint(s: number | null): string | undefined {
  if (s === null) return undefined;
  if (s > 0.20) return "text-[var(--warning)]";
  if (s > 0.10) return "text-[var(--info)]";
  return undefined;
}

function earningsHint(days: number | null): string | undefined {
  if (days === null) return undefined;
  if (days >= 0 && days <= 7) return "text-[var(--warning)]";
  return undefined;
}
