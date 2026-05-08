import type { LensView, LensName } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Multi-lens analyst panel — four independent reads on the trade setup.
 *
 * Quant / Fundamental / Sentiment-Macro / Contrarian-Risk. Each lens is
 * shown side-by-side at equal weight so competing perspectives stay
 * legible. The user (or a future Judge agent) does the synthesis; the
 * panel does NOT collapse them into one verdict.
 *
 * In Quick mode all four come from a single LLM call. In Deep mode (Phase 2)
 * each lens is replaced by a dedicated agent — schema and UI stay identical.
 */
export function LensPanel({ lenses }: { lenses: LensView[] }) {
  if (!lenses || lenses.length === 0) return null;

  // Order is fixed for visual consistency; missing lenses just don't render.
  const order: LensName[] = [
    "quantitative",
    "fundamental",
    "sentiment_macro",
    "contrarian_risk",
  ];
  const byName = new Map(lenses.map((l) => [l.name, l]));
  const ordered = order.map((n) => byName.get(n)).filter(Boolean) as LensView[];

  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm tracking-tight">Analyst panel · 4 lenses</h2>
        <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          Independent reads · {summariseDirections(ordered)}
        </p>
      </header>
      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-[var(--hairline-2)] border-b border-[var(--hairline-2)]">
        {ordered.map((lens) => (
          <LensCard key={lens.name} lens={lens} />
        ))}
      </div>
    </section>
  );
}

function LensCard({ lens }: { lens: LensView }) {
  const dirColor =
    lens.direction === "bullish"
      ? "text-[var(--positive)]"
      : lens.direction === "bearish"
        ? "text-[var(--negative)]"
        : "text-[var(--muted-foreground)]";
  return (
    <div className="p-4">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--muted-2)]">
          {lensLabel(lens.name)}
        </h3>
        <span
          className={cn(
            "text-[11px] uppercase tracking-wider",
            dirColor,
          )}
        >
          [{lens.direction}] · {lens.conviction}
        </span>
      </div>
      <p className="text-xs text-[var(--foreground)] leading-relaxed mb-2">
        {lens.summary || "—"}
      </p>
      {lens.points && lens.points.length > 0 && (
        <ul className="space-y-0.5 text-[11px] text-[var(--muted-foreground)]">
          {lens.points.map((p, i) => (
            <li key={i}>· {p}</li>
          ))}
        </ul>
      )}
      {lens.revised_summary ? (
        <div className="mt-3 pt-2 border-t border-[var(--hairline-2)] space-y-1">
          <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--accent)]">
            Revised after debate
          </div>
          <p className="text-xs text-[var(--foreground)] leading-relaxed">
            {lens.revised_summary}
          </p>
          {lens.revised_points && lens.revised_points.length > 0 && (
            <ul className="space-y-0.5 text-[11px] text-[var(--muted-foreground)]">
              {lens.revised_points.map((p, i) => (
                <li key={i}>· {p}</li>
              ))}
            </ul>
          )}
          {lens.responded_to && lens.responded_to.length > 0 && (
            <div className="text-[10px] uppercase tracking-wider text-[var(--muted-2)]">
              Responded to: {lens.responded_to.join(", ")}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function lensLabel(name: LensName): string {
  switch (name) {
    case "quantitative":
      return "Quantitative";
    case "fundamental":
      return "Fundamental";
    case "sentiment_macro":
      return "Sentiment / Macro";
    case "contrarian_risk":
      return "Contrarian / Risk";
  }
}

function summariseDirections(lenses: LensView[]): string {
  const bull = lenses.filter((l) => l.direction === "bullish").length;
  const bear = lenses.filter((l) => l.direction === "bearish").length;
  const neut = lenses.filter((l) => l.direction === "neutral").length;
  const parts: string[] = [];
  if (bull) parts.push(`${bull} bullish`);
  if (neut) parts.push(`${neut} neutral`);
  if (bear) parts.push(`${bear} bearish`);
  return parts.join(" · ");
}
