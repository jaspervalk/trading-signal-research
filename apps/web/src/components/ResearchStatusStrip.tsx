import type { DecisionStatus, SetupType, TickerResearchView } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ActionBadge } from "@/components/ActionBadge";

/**
 * Headline strip immediately under the page header. Action label + status +
 * setup + style + summary. Sized to be impossible to miss — this is the
 * "is this worth researching, and what does it imply?" answer.
 *
 * The action label is the prominent left-side badge per ADR 0008; the
 * decision-support status sits next to it as the auditable substrate.
 */
export function ResearchStatusStrip({ view }: { view: TickerResearchView }) {
  const { status, setup, style_fit, action } = view;
  return (
    <section
      className={cn(
        "bg-[var(--panel)] border-l-4 px-5 py-4 font-mono-jb",
        statusBorder(status.status),
      )}
    >
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
              · status conf {status.confidence}
              {action.rubric_pass_rate !== null && (
                <> · rubric {Math.round(action.rubric_pass_rate * 100)}%</>
              )}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">
          <Tag>setup: {setup.setup_type.replace(/_/g, " ")}</Tag>
          <Tag>style: {(style_fit.primary_style ?? "—").replace(/_/g, " ")}</Tag>
          <Tag>setup conf: {setup.confidence}</Tag>
        </div>
      </div>
      <p className="mt-2 text-[13px] text-[var(--foreground)] leading-relaxed">
        {status.summary}
      </p>
      <p className="mt-1.5 text-[11px] uppercase tracking-wider text-[var(--muted-2)]">
        derived: {action.derivation}
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
    </section>
  );
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

function statusBorder(status: DecisionStatus): string {
  switch (status) {
    case "research_candidate":
      return "border-[var(--positive)]";
    case "watch":
      return "border-[var(--info)]";
    case "wait_for_setup":
      return "border-[var(--border)]";
    case "skip_for_now":
      return "border-[var(--negative)]";
    case "extended_risk":
      return "border-[var(--warning)]";
    case "insufficient_data":
      return "border-[var(--border)]";
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
