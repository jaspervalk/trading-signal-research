"use client";

import { useState } from "react";

import { ClaimsFeed } from "@/components/ClaimsFeed";
import { CreatorCoverage } from "@/components/CreatorCoverage";
import { LiveSignalStrip } from "@/components/LiveSignalStrip";
import { TickerCallsTable } from "@/components/TickerCallsTable";
import type { TickerCall } from "@/lib/api";

/**
 * Every creator-derived surface, gathered into one collapsed section.
 *
 * The YouTube pipeline is a side feature as of ADR 0009: one signal source
 * among several. It previously occupied the top-left rail, the centre column
 * and half the right rail, which read as though transcript mentions were the
 * point of the page. Collapsed by default, with the count on the summary line
 * so an empty corpus costs no attention and a busy one still invites a click.
 */
export function CreatorSignalsSection({
  ticker,
  calls,
  claimCount,
}: {
  ticker: string;
  calls: TickerCall[];
  claimCount: number;
}) {
  const [open, setOpen] = useState(false);
  const total = calls.length + claimCount;

  return (
    <section className="bg-[var(--panel-2)] border border-[var(--border)] font-mono-jb">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full px-4 py-3 flex items-center justify-between gap-3 text-left hover:bg-[var(--panel)] transition-colors duration-150 focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--info)]"
      >
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-sm tracking-tight text-[var(--muted-foreground)]">
            Creator signals
          </h2>
          <span className="text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
            {total === 0
              ? "no transcript coverage"
              : `${calls.length} call${calls.length === 1 ? "" : "s"} · ${claimCount} claim${claimCount === 1 ? "" : "s"}`}
          </span>
        </div>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] shrink-0">
          {open ? "hide" : "show"}
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--hairline-2)] p-4 space-y-4">
          <p className="text-[11px] text-[var(--muted-2)] leading-relaxed max-w-[70ch]">
            Transcript-derived signals are one input among several and are not
            weighted into the decision rubric. Sample sizes here are small; treat
            them as suggestive.
          </p>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            <div className="space-y-4">
              <LiveSignalStrip ticker={ticker} />
              <CreatorCoverage ticker={ticker} />
            </div>
            <div className="space-y-4">
              <TickerCallsTable calls={calls} />
              <ClaimsFeed ticker={ticker} />
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
