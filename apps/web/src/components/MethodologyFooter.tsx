import Link from "next/link";

import type { TickerSignal } from "@/lib/api";
import { fmtDateTime, fmtIsoDate } from "@/lib/utils";

/**
 * IA section 8: methodology / data-state footer. The Tape-direction showpiece —
 * 3-col grid (pipeline state | definitions | ADR links) plus the full-width
 * LOW-N WARNING banner.
 *
 * The banner has two modes:
 *   - active (props.isLowN === true): chunky amber border, no hatch overlay
 *   - inactive demo (default): same chrome with a 45° hatch overlay and a
 *     `[N=… · ABOVE THRESHOLD · BANNER SUPPRESSED IN PRODUCTION]` caption,
 *     so the warning treatment is documented and visible even when N is fine.
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
  const lowN = totalMentions < 5;
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

      <LowNBanner isActive={lowN} totalMentions={totalMentions} />
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

/**
 * Full-width chunky-bordered banner. Lives at the bottom of the methodology
 * section so the warning treatment is visible by default — see ADR 0005's
 * epistemic-honesty-as-design rule.
 */
function LowNBanner({
  isActive,
  totalMentions,
}: {
  isActive: boolean;
  totalMentions: number;
}) {
  return (
    <div
      className={`relative border-t-4 border-[var(--warning)] bg-[var(--panel)] px-6 py-5 ${
        isActive ? "" : "demo-hatch"
      }`}
    >
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <p className="text-[var(--warning)] font-bold uppercase tracking-wider text-[15px] md:text-[18px] leading-tight">
          Low-N warning — treat as suggestive, not actionable — total mentions
          below threshold
        </p>
        <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
          {isActive ? "[ACTIVE]" : "[DEMO_RENDER · INACTIVE_STATE]"}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] text-[var(--muted-foreground)]">
        {isActive
          ? `[N=${totalMentions} · BELOW THRESHOLD (5) · BANNER FIRING]`
          : `[N=${totalMentions} · ABOVE THRESHOLD · BANNER SUPPRESSED IN PRODUCTION]`}
      </p>
    </div>
  );
}
