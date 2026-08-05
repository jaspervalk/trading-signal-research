import Link from "next/link";

import type { TickerSignal } from "@/lib/api";
import { fmtDateTime, fmtIsoDate } from "@/lib/utils";

/**
 * Methodology and data-state footer: pipeline state, definitions, ADR links.
 *
 * The full-width amber LOW-N banner that used to close this section was
 * removed in 2026-08. It shouted about transcript sample size on a page whose
 * verdict never depended on transcripts, and it fired on every ticker with
 * fewer than five mentions, which is nearly all of them. The same caveat now
 * sits inside the collapsed creator section, next to the data it qualifies.
 */
export function MethodologyFooter({
  totalMentions,
  signals,
  lastExtractorRun,
}: {
  totalMentions: number;
  signals: TickerSignal[] | undefined;
  lastExtractorRun: string | null;
}) {
  const lastAggregate = signals && signals.length > 0 ? signals[0].computed_at : null;
  const aggregatorVersion =
    signals && signals.length > 0 ? signals[0].aggregator_version : null;

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-5 py-3 border-b border-[var(--hairline-2)] text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
        Methodology &amp; data state
      </header>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-5 p-5">
        <DataStateCol
          lastExtractorRun={lastExtractorRun}
          lastAggregate={lastAggregate}
          aggregatorVersion={aggregatorVersion}
          totalMentions={totalMentions}
        />
        <DefinitionsCol />
        <AdrLinksCol />
      </div>
    </section>
  );
}

function DataStateCol({
  lastExtractorRun,
  lastAggregate,
  aggregatorVersion,
  totalMentions,
}: {
  lastExtractorRun: string | null;
  lastAggregate: string | null;
  aggregatorVersion: string | null;
  totalMentions: number;
}) {
  return (
    <div className="space-y-1.5 text-[11px] text-[var(--muted-foreground)]">
      <h3 className="text-[11px] uppercase tracking-wider text-[var(--foreground)] mb-2">
        Pipeline state
      </h3>
      <div>
        [LAST_EXTRACTOR_RUN:{" "}
        <span className="text-[var(--foreground)]">
          {lastExtractorRun ? fmtIsoDate(lastExtractorRun) : "—"}
        </span>
        ]
      </div>
      <div>
        [LAST_AGGREGATE_SIGNALS:{" "}
        <span className="text-[var(--foreground)]">
          {lastAggregate ? fmtDateTime(lastAggregate) : "—"}
        </span>
        ]
      </div>
      <div>
        [TOTAL_MENTIONS:{" "}
        <span className="text-[var(--foreground)]">{totalMentions}</span>]
      </div>
      {aggregatorVersion && (
        <div>
          [AGGREGATOR_VERSION:{" "}
          <span className="text-[var(--foreground)]">{aggregatorVersion}</span>]
        </div>
      )}
    </div>
  );
}

function DefinitionsCol() {
  return (
    <div className="space-y-1.5 text-[11px] text-[var(--muted-foreground)] leading-relaxed">
      <h3 className="text-[11px] uppercase tracking-wider text-[var(--foreground)] mb-2">
        Definitions
      </h3>
      <div>
        <span className="text-[var(--info)]">claims_all</span> — every accepted
        claim mentioning this ticker, regardless of class.
      </div>
      <div>
        <span className="text-[var(--info)]">claims_factual</span> — subset where{" "}
        <code>claim_class = factual</code>.
      </div>
      <div>
        <span className="text-[var(--info)]">creator_consensus</span> —
        n_distinct_creators × polarity agreement signal.
      </div>
      <div>
        <span className="text-[var(--info)]">credibility_weighted_polarity</span>{" "}
        — net polarity weighted by each creator&apos;s most-recent
        CreatorScorecard hit_rate_lower_ci.
      </div>
      <div>
        <span className="text-[var(--info)]">Wilson 95% CI</span> — bracket
        notation; lower bound is the conservative read.
      </div>
    </div>
  );
}

function AdrLinksCol() {
  return (
    <div className="space-y-1.5 text-[11px] text-[var(--muted-foreground)]">
      <h3 className="text-[11px] uppercase tracking-wider text-[var(--foreground)] mb-2">
        Methodology references
      </h3>
      <div>
        <Link href="/methodology" className="text-[var(--info)] hover:underline">
          → ADR-0005 · Product pivot to decision-support
        </Link>
      </div>
      <div>
        <Link href="/methodology" className="text-[var(--info)] hover:underline">
          → ADR-0006 · Claims and ticker signals
        </Link>
      </div>
      <div>
        <Link href="/methodology" className="text-[var(--info)] hover:underline">
          → ADR-0003 · Backtest assumptions
        </Link>
      </div>
      <div>
        <Link href="/methodology" className="text-[var(--info)] hover:underline">
          → Disclaimer · Research only, not investment advice
        </Link>
      </div>
    </div>
  );
}
