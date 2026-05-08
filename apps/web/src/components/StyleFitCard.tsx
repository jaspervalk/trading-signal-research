import type { ResearchStyleFit, ResearchStyleFitItem } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Style fit — which research style does the current setup most resemble?
 * Right rail. Each style row shows fit + reasons; only "high" / "medium"
 * fits are expanded by default.
 */
export function StyleFitCard({ styleFit }: { styleFit: ResearchStyleFit }) {
  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm tracking-tight">Style fit</h2>
        <p className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          primary: {(styleFit.primary_style ?? "—").replace(/_/g, " ")}
        </p>
      </header>
      <ul>
        {styleFit.items.map((it) => (
          <StyleRow key={it.style} item={it} />
        ))}
      </ul>
    </section>
  );
}

function StyleRow({ item }: { item: ResearchStyleFitItem }) {
  const muted = item.fit_level === "low";
  return (
    <li
      className={cn(
        "border-b border-[var(--hairline-2)] last:border-b-0 px-4 py-2.5 text-[11px]",
        muted && "opacity-60",
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[var(--foreground)]">
          {item.style.replace(/_/g, " ")}
        </span>
        <span
          className={cn(
            "text-[11px] uppercase tracking-[0.12em]",
            fitColor(item.fit_level),
          )}
        >
          [fit: {item.fit_level}]
        </span>
      </div>
      {!muted && item.reasons.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-[var(--muted-foreground)]">
          {item.reasons.slice(0, 3).map((r, i) => (
            <li key={i}>· {r}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function fitColor(level: "high" | "medium" | "low"): string {
  switch (level) {
    case "high":
      return "text-[var(--positive)]";
    case "medium":
      return "text-[var(--info)]";
    case "low":
      return "text-[var(--muted-2)]";
  }
}
