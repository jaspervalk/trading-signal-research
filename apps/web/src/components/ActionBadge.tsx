import type { ActionLabel, ResearchAction } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Implied buy/hold/sell badge — derived view over `DecisionSupportStatus`.
 *
 * See ADR 0008. The badge shows the label + a `[from <derivation>]` hint, so
 * the underlying rubric is always one step away. Colors follow the shared
 * status palette so a BUY card and a research_candidate strip stay visually
 * coherent.
 */
export function ActionBadge({
  action,
  size = "md",
}: {
  action: ResearchAction;
  size?: "sm" | "md" | "lg";
}) {
  const sizing =
    size === "lg"
      ? "text-lg px-3 py-1.5 tracking-[0.18em]"
      : size === "sm"
        ? "text-[11px] px-2 py-0.5 tracking-[0.14em]"
        : "text-sm px-2.5 py-1 tracking-[0.16em]";
  return (
    <span
      className={cn(
        "inline-flex items-center font-bold uppercase border font-mono-jb",
        sizing,
        labelClasses(action.label),
      )}
      title={action.derivation}
    >
      {action.label}
    </span>
  );
}

function labelClasses(label: ActionLabel): string {
  switch (label) {
    case "BUY":
      return "text-[var(--positive)] border-[var(--positive)] bg-[color:rgba(34,197,94,0.10)]";
    case "ACCUMULATE":
      return "text-[var(--positive)] border-[color:rgba(34,197,94,0.45)] bg-[color:rgba(34,197,94,0.05)]";
    case "HOLD":
      return "text-[var(--info)] border-[color:rgba(34,211,238,0.45)] bg-[color:rgba(34,211,238,0.05)]";
    case "WAIT":
      return "text-[var(--muted-foreground)] border-[var(--border)] bg-[var(--panel-2)]";
    case "REDUCE":
      return "text-[var(--warning)] border-[var(--warning)] bg-[color:rgba(245,158,11,0.08)]";
    case "AVOID":
      return "text-[var(--negative)] border-[var(--negative)] bg-[color:rgba(239,68,68,0.08)]";
    case "N/A":
      return "text-[var(--muted-2)] border-[var(--border)] bg-transparent";
  }
}
