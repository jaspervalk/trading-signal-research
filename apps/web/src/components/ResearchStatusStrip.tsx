import type { DecisionStatus, SetupType, TickerResearchView } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ActionBadge } from "@/components/ActionBadge";

/**
 * Headline strip under the page header: the "what does this add up to" answer.
 *
 * Two design constraints, both learned the hard way.
 *
 * The status colour is carried by a top rule and the label itself rather than a
 * thick left border, which reads as decoration rather than meaning.
 *
 * When there is no verdict to give, the strip says what is missing instead of
 * reciting three synonyms for "unknown" plus a 0% score. A rubric that scored
 * nothing is not a rubric that scored zero, and rendering it as "0%" invited
 * exactly the wrong reading.
 */
export function ResearchStatusStrip({
  view,
  firstBarDate,
}: {
  view: TickerResearchView;
  /** Earliest bar we hold, used to tell a young listing from a fetch failure. */
  firstBarDate?: string | null;
}) {
  const { status, setup, style_fit, action, identity } = view;
  const unknown = status.status === "insufficient_data";

  if (unknown) return <InsufficientData view={view} firstBarDate={firstBarDate ?? null} />;

  const rubric = action.rubric_pass_rate;

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <div className={cn("h-px w-full", statusRule(status.status))} />
      <div className="px-5 py-4">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            <ActionBadge action={action} size="lg" />
            <div className="flex items-baseline gap-2 flex-wrap">
              <span
                className={cn(
                  "text-sm font-medium uppercase tracking-[0.14em]",
                  statusText(status.status),
                )}
              >
                {status.status.replace(/_/g, " ")}
              </span>
              <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
                · confidence {status.confidence}
                {rubric !== null && (
                  <>
                    {" "}
                    · {Math.round(rubric * 100)}% of checks passed
                  </>
                )}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
            <Tag>
              setup:{" "}
              <span className={setupColor(setup.setup_type)}>
                {setup.setup_type.replace(/_/g, " ")}
              </span>
            </Tag>
            <Tag>style: {(style_fit.primary_style ?? "none").replace(/_/g, " ")}</Tag>
          </div>
        </div>

        <p className="mt-2 text-[13px] leading-relaxed max-w-[75ch]">{status.summary}</p>
        <p className="mt-1.5 text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
          computed from price history · {action.derivation}
        </p>

        {(status.caveats.length > 0 || action.notes.length > 0) && (
          <ul className="mt-2 space-y-0.5 text-[11px] text-[var(--muted-foreground)]">
            {status.caveats.map((c, i) => (
              <li key={`s${i}`}>· {c}</li>
            ))}
            {action.notes.map((n, i) => (
              <li key={`a${i}`}>· {n}</li>
            ))}
          </ul>
        )}

        {identity.data_freshness_days !== null && identity.data_freshness_days > 5 && (
          <p className="mt-2 text-[11px] uppercase tracking-wider text-[var(--warning)]">
            Price data is {identity.data_freshness_days} days old
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * The no-verdict state. Names the blocker and the observable that would clear
 * it, rather than restating "unknown" three ways.
 */
function InsufficientData({
  view,
  firstBarDate,
}: {
  view: TickerResearchView;
  firstBarDate: string | null;
}) {
  const { identity, market } = view;
  const bars = identity.n_bars_loaded;
  const needed = 200;

  // A ticker that has simply not existed long enough is a different situation
  // from one whose data failed to load, and the remedy is different: waiting
  // versus investigating. Roughly 21 sessions a month.
  const youngListing =
    bars > 0 && firstBarDate !== null && bars < needed && bars <= monthsSince(firstBarDate) * 23;

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <div className="h-px w-full bg-[var(--border)]" />
      <div className="px-5 py-4 space-y-2">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-sm font-medium uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
            No verdict yet
          </span>
          <span className="text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
            · the rubric did not run
          </span>
        </div>

        <p className="text-[13px] leading-relaxed max-w-[75ch]">
          {bars === 0 ? (
            <>
              No price history loaded for this ticker, so nothing downstream can be
              computed. The technicals, setup and entry panels below will stay empty
              until bars land.
            </>
          ) : youngListing ? (
            <>
              This ticker has only traded since{" "}
              <span className="tabular-nums">{firstBarDate?.slice(0, 10)}</span>, which
              is all {bars} sessions of it. The trend rubric needs {needed} for a
              200-day average, so a verdict is roughly{" "}
              {Math.max(1, Math.ceil((needed - bars) / 21))} months away. Expected for
              a recent listing, not a data problem.
            </>
          ) : (
            <>
              {bars} daily bars loaded; the trend rubric needs {needed} for a
              200-day average. Everything that depends on long-horizon trend is
              withheld rather than estimated from a short series.
            </>
          )}
        </p>

        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[11px] pt-1">
          <dt className="text-[var(--muted-foreground)] uppercase tracking-wider">
            bars loaded
          </dt>
          <dd className="tabular-nums">
            {bars} / {needed} needed
          </dd>
          {market.last_close !== null && (
            <>
              <dt className="text-[var(--muted-foreground)] uppercase tracking-wider">
                last close
              </dt>
              <dd className="tabular-nums">${market.last_close.toFixed(2)}</dd>
            </>
          )}
          {identity.data_freshness_days !== null && (
            <>
              <dt className="text-[var(--muted-foreground)] uppercase tracking-wider">
                newest bar
              </dt>
              <dd className="tabular-nums">
                {identity.data_freshness_days} day
                {identity.data_freshness_days === 1 ? "" : "s"} old
              </dd>
            </>
          )}
        </dl>

        {identity.missing_data_warnings.length > 0 && (
          <ul className="space-y-0.5 text-[11px] text-[var(--muted-foreground)] pt-1">
            {identity.missing_data_warnings.map((w, i) => (
              <li key={i}>· {w}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function monthsSince(iso: string): number {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return Number.POSITIVE_INFINITY;
  return (Date.now() - then) / (1000 * 60 * 60 * 24 * 30.4);
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-1.5 py-0.5 border border-[var(--border)] text-[var(--muted-foreground)]">
      [{children}]
    </span>
  );
}

function statusText(status: DecisionStatus): string {
  switch (status) {
    case "research_candidate":
      return "text-[var(--positive)]";
    case "watch":
      return "text-[var(--info)]";
    case "wait_for_setup":
      return "text-[var(--muted-foreground)]";
    case "skip_for_now":
      return "text-[var(--negative)]";
    case "extended_risk":
      return "text-[var(--warning)]";
    case "insufficient_data":
      return "text-[var(--muted-foreground)]";
  }
}

/** Status colour as a hairline rule across the top, never a side stripe. */
function statusRule(status: DecisionStatus): string {
  switch (status) {
    case "research_candidate":
      return "bg-[var(--positive)]";
    case "watch":
      return "bg-[var(--info)]";
    case "wait_for_setup":
      return "bg-[var(--border)]";
    case "skip_for_now":
      return "bg-[var(--negative)]";
    case "extended_risk":
      return "bg-[var(--warning)]";
    case "insufficient_data":
      return "bg-[var(--border)]";
  }
}

export function setupColor(setup: SetupType): string {
  switch (setup) {
    case "strong_uptrend":
    case "breakout_candidate":
      return "text-[var(--positive)]";
    case "uptrend_pullback":
      return "text-[var(--info)]";
    case "extended_momentum":
      return "text-[var(--warning)]";
    case "downtrend":
    case "low_liquidity":
    case "high_volatility_unstable":
      return "text-[var(--negative)]";
    default:
      return "text-[var(--muted-foreground)]";
  }
}
