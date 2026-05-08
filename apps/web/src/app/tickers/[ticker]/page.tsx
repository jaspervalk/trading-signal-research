"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Annotations } from "@/components/Annotations";
import { Badge } from "@/components/Card";
import { ClaimsFeed } from "@/components/ClaimsFeed";
import { CreatorCoverage } from "@/components/CreatorCoverage";
import { EntryExitPanel } from "@/components/EntryExitPanel";
import { EntryZoneCard } from "@/components/EntryZoneCard";
import { LiveSignalStrip } from "@/components/LiveSignalStrip";
import { MethodologyFooter } from "@/components/MethodologyFooter";
import { PriceChart } from "@/components/PriceChart";
import { ResearchStatusStrip } from "@/components/ResearchStatusStrip";
import { SetupCard } from "@/components/SetupCard";
import { StyleFitCard } from "@/components/StyleFitCard";
import { Tags } from "@/components/Tags";
import { TechnicalsCard } from "@/components/TechnicalsCard";
import { TickerCallsTable } from "@/components/TickerCallsTable";
import { ValuationPanel } from "@/components/ValuationPanel";
import { WatchlistButton } from "@/components/WatchlistButton";
import { api } from "@/lib/api";
import { cn, daysSince, fmtPct, pctColor } from "@/lib/utils";

/**
 * Ticker detail page — the headline view of the dashboard per ADR 0005.
 * 8-section composite per docs/dashboard-ticker-page-ia.md.
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
    // Yahoo bar fetch + benchmark fetch + analysis composition is ~1-3s on cold cache.
    staleTime: 60_000,
  });

  // Header price/day-change is derived from the last two daily bars.
  const lastBar = bars?.bars.at(-1);
  const prevBar = bars?.bars.at(-2);
  const dayChangePct =
    lastBar && prevBar ? (lastBar.close - prevBar.close) / prevBar.close : null;
  const dayChangeAbs = lastBar && prevBar ? lastBar.close - prevBar.close : null;

  // Most-recent mention across calls + claims drives the "historical only" gate.
  const mentionTimes: number[] = [
    ...(calls?.map((c) => new Date(c.posted_at).getTime()) ?? []),
    ...(claims?.map((c) => new Date(c.posted_at).getTime()) ?? []),
  ];
  const mostRecentMention = mentionTimes.length > 0 ? new Date(Math.max(...mentionTimes)) : null;
  const mostRecentMentionDays = daysSince(mostRecentMention);
  const isHistoricalOnly =
    mostRecentMentionDays !== null && mostRecentMentionDays > 90;

  const totalMentions = (calls?.length ?? 0) + (claims?.length ?? 0);
  const lastExtractorRun = claims?.[0]?.extracted_at ?? null;

  return (
    <div className="space-y-4 font-mono-jb">
      {/* Section 1 — Header strip */}
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1.5">
          <Link
            href="/tickers"
            className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] hover:text-[var(--info)]"
          >
            ← Tickers
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
            {isHistoricalOnly && (
              <Badge className="text-[var(--warning)] border-[color:rgba(245,158,11,0.4)] bg-[color:rgba(245,158,11,0.06)] uppercase tracking-wider">
                Historical only · {mostRecentMentionDays}d ago
              </Badge>
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

      {/* Research status strip — the headline answer */}
      {research && <ResearchStatusStrip view={research} />}
      {researchError && (
        <p className="text-[11px] uppercase tracking-wider text-[var(--negative)] font-mono-jb">
          Research API error · {(researchError as Error).message}
        </p>
      )}

      {/* Three-column grid */}
      <div className="grid grid-cols-1 xl:grid-cols-[340px_minmax(0,1fr)_360px] gap-5 items-start">
        {/* Left rail — Live signals + technicals */}
        <div className="space-y-4">
          <LiveSignalStrip ticker={ticker} />
          {research && (
            <TechnicalsCard
              indicators={research.indicators}
              levels={research.levels}
              market={research.market}
            />
          )}
        </div>

        {/* Center — chart + setup + entry zone + claims feed */}
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
                <PriceChart bars={bars.bars} calls={calls ?? []} />
              )}
            </div>
          </section>
          {research && (
            <SetupCard setup={research.setup} status={research.status} />
          )}
          {research && <EntryZoneCard entry={research.entry_zone} />}
          <EntryExitPanel ticker={ticker} />
          <ClaimsFeed ticker={ticker} />
        </div>

        {/* Right rail — Style fit + valuation + calls + coverage + notes */}
        <div className="space-y-4">
          {research && <StyleFitCard styleFit={research.style_fit} />}
          {research && <ValuationPanel valuation={research.valuation} />}
          <TickerCallsTable calls={calls ?? []} />
          <CreatorCoverage ticker={ticker} />
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

      {/* Section 8 — Methodology / data-state footer */}
      <MethodologyFooter
        totalMentions={totalMentions}
        signals={signals}
        lastExtractorRun={lastExtractorRun}
      />
    </div>
  );
}
