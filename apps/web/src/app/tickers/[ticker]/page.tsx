"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Annotations } from "@/components/Annotations";
import { CreatorSignalsSection } from "@/components/CreatorSignalsSection";
import { EntryExitPanel } from "@/components/EntryExitPanel";
import { EntryZoneCard } from "@/components/EntryZoneCard";
import { MethodologyFooter } from "@/components/MethodologyFooter";
import { PositionContext } from "@/components/PositionContext";
import { PriceChart } from "@/components/PriceChart";
import { ResearchStatusStrip } from "@/components/ResearchStatusStrip";
import { SetupCard } from "@/components/SetupCard";
import { StyleFitCard } from "@/components/StyleFitCard";
import { Tags } from "@/components/Tags";
import { TechnicalsCard } from "@/components/TechnicalsCard";
import { ValuationPanel } from "@/components/ValuationPanel";
import { WatchlistButton } from "@/components/WatchlistButton";
import { api } from "@/lib/api";
import { cn, fmtPct, pctColor } from "@/lib/utils";

/**
 * Ticker research page.
 *
 * Ranked quant-first per ADR 0009: price and position at the top, then the
 * deterministic read (technicals, setup, levels), then the costed LLM panel,
 * then valuation. Every creator-derived surface is collapsed into one section
 * near the bottom; the transcript pipeline is a side feature and the layout
 * should say so.
 */
export default function TickerDetailPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = params.ticker.toUpperCase();

  const { data: bars } = useQuery({
    queryKey: ["ticker-bars", ticker],
    queryFn: () => api.tickers.bars(ticker, 365),
  });
  const { data: calls } = useQuery({
    queryKey: ["ticker-calls", ticker],
    queryFn: () => api.tickers.calls(ticker),
  });
  const { data: claims } = useQuery({
    queryKey: ["ticker-claims", ticker],
    queryFn: () => api.tickers.claims(ticker, { limit: 50 }),
  });
  const { data: signals } = useQuery({
    queryKey: ["ticker-signals", ticker],
    queryFn: () => api.tickers.signals(ticker),
  });
  const { data: research, error: researchError } = useQuery({
    queryKey: ["ticker-research", ticker],
    queryFn: () => api.tickers.research(ticker, { fetch_metadata: true }),
    staleTime: 60_000,
  });
  // Shared with PositionContext via the same query key, so the chart can draw
  // your cost line without a second request.
  const { data: portfolio } = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api.portfolio.get(true),
    staleTime: 60_000,
  });
  const held = portfolio?.open_positions.find((p) => p.ticker === ticker) ?? null;

  const lastBar = bars?.bars.at(-1);
  const prevBar = bars?.bars.at(-2);
  const firstBar = bars?.bars.at(0);
  const dayChangePct =
    lastBar && prevBar ? (lastBar.close - prevBar.close) / prevBar.close : null;
  const dayChangeAbs = lastBar && prevBar ? lastBar.close - prevBar.close : null;

  const totalMentions = (calls?.length ?? 0) + (claims?.length ?? 0);
  const lastExtractorRun = claims?.[0]?.extracted_at ?? null;

  return (
    <div className="space-y-4 font-mono-jb">
      {/* Header — identity and price */}
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1.5">
          <Link
            href="/"
            className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] hover:text-[var(--info)]"
          >
            ← Portfolio
          </Link>
          <div className="flex items-baseline gap-4 flex-wrap">
            <h1 className="text-4xl font-semibold tracking-tight">{ticker}</h1>
            {lastBar && (
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-medium tabular-nums">
                  ${lastBar.close.toFixed(2)}
                </span>
                {dayChangeAbs !== null && dayChangePct !== null && (
                  <span className={cn("text-sm tabular-nums", pctColor(dayChangePct))}>
                    {dayChangeAbs > 0 ? "+" : ""}
                    {dayChangeAbs.toFixed(2)} {fmtPct(dayChangePct)}
                  </span>
                )}
              </div>
            )}
            {research?.identity.name && (
              <span className="text-sm text-[var(--muted-foreground)]">
                {research.identity.name}
              </span>
            )}
          </div>
          <Link
            href="/methodology"
            className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] hover:text-[var(--info)]"
          >
            Decision support, not advice — see methodology →
          </Link>
        </div>
        <WatchlistButton entityType="ticker" entityId={ticker} />
      </header>

      {/* The headline answer */}
      {research && (
        <ResearchStatusStrip
          view={research}
          firstBarDate={
            firstBar ? new Date(firstBar.time * 1000).toISOString() : null
          }
        />
      )}
      {researchError && (
        <p className="text-[11px] uppercase tracking-wider text-[var(--negative)]">
          Research API error · {(researchError as Error).message}
        </p>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[340px_minmax(0,1fr)_360px] gap-5 items-start">
        {/* Left — your stake, then the deterministic read */}
        <div className="space-y-4">
          <PositionContext
            ticker={ticker}
            invalidation={research?.entry_zone.invalidation_reference ?? null}
          />
          {research && (
            <TechnicalsCard
              indicators={research.indicators}
              levels={research.levels}
              market={research.market}
            />
          )}
        </div>

        {/* Centre — price, setup, levels, then the costed panel */}
        <div className="space-y-4 min-w-0">
          <section className="bg-[var(--panel)] border border-[var(--border)]">
            <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
              <h2 className="text-sm tracking-tight">Price · last 365 days</h2>
              <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
                Markers at extracted-call timestamps · color = 5d return
              </p>
            </header>
            <div className="p-3">
              {!bars || bars.bars.length === 0 ? (
                <p className="text-xs text-[var(--muted-foreground)] py-8 text-center">
                  No price data — yfinance returned nothing for this ticker.
                </p>
              ) : (
                <PriceChart
                  bars={bars.bars}
                  calls={calls ?? []}
                  avgCost={held?.avg_cost ?? null}
                  invalidation={research?.entry_zone.invalidation_reference ?? null}
                />
              )}
            </div>
          </section>
          {research && <SetupCard setup={research.setup} status={research.status} />}
          {research && <EntryZoneCard entry={research.entry_zone} />}
          <EntryExitPanel ticker={ticker} />
        </div>

        {/* Right — valuation, style fit, notes */}
        <div className="space-y-4">
          {research && <ValuationPanel valuation={research.valuation} />}
          {research && <StyleFitCard styleFit={research.style_fit} />}
          <section className="bg-[var(--panel)] border border-[var(--border)]">
            <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
              <h2 className="text-sm tracking-tight">Notes &amp; review</h2>
              <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
                Personal scratchpad · not synced
              </p>
            </header>
            <div className="p-4 space-y-4">
              <Annotations entityType="ticker" entityId={ticker} />
              <Tags entityType="ticker" entityId={ticker} />
            </div>
          </section>
        </div>
      </div>

      {/* Side feature — collapsed, and last */}
      <CreatorSignalsSection
        ticker={ticker}
        calls={calls ?? []}
        claimCount={claims?.length ?? 0}
      />

      <MethodologyFooter
        totalMentions={totalMentions}
        signals={signals}
        lastExtractorRun={lastExtractorRun}
      />
    </div>
  );
}
