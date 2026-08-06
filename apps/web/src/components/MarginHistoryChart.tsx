"use client";

import type { SupplyMarginHistory } from "@/lib/api";

/**
 * The one chart that matters on the supply screen: quarterly gross margin
 * over time, with the current value and the historical peak both marked so
 * the headroom between them reads as a distance on the page, not a
 * subtraction the reader has to do themselves.
 *
 * Hand-rolled inline SVG, no charting library — mirrors the discipline in
 * `FactorConcentration.tsx`. Deliberately NOT a price chart: gross margin
 * only, quarterly cadence, no candles, no volume. Price action is out of
 * scope everywhere on this page by design.
 */
export function MarginHistoryChart({ history }: { history: SupplyMarginHistory }) {
  const { points, current_gross_margin, historical_peak_gross_margin, headroom_pp } = history;

  if (points.length === 0) {
    return (
      <p className="text-[11px] text-[var(--muted-foreground)] font-mono-jb">
        No usable quarterly gross-margin history for {history.ticker} — EDGAR
        returned {history.quarters} quarter(s), not enough to chart.
      </p>
    );
  }

  const W = 640;
  const H = 180;
  const padX = 8;
  const padY = 16;

  const values = points.map((p) => p.gross_margin);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = Math.max(hi - lo, 0.0001);

  const x = (i: number) =>
    points.length === 1 ? W / 2 : padX + (i / (points.length - 1)) * (W - padX * 2);
  const y = (v: number) => H - padY - ((v - lo) / span) * (H - padY * 2);

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(p.gross_margin).toFixed(1)}`).join(" ");

  const peakY = historical_peak_gross_margin !== null ? y(historical_peak_gross_margin) : null;
  const currentIdx = points.length - 1;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-4">
        <Stat label="Current GM" value={pct(current_gross_margin)} />
        <Stat label="Historical peak" value={pct(historical_peak_gross_margin)} />
        <Stat
          label="Headroom"
          value={headroom_pp !== null ? `${headroom_pp.toFixed(1)}pp` : "—"}
          accent
        />
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-40 overflow-visible"
        role="img"
        aria-label={`Quarterly gross margin history for ${history.ticker}`}
      >
        {/* Historical-peak reference line. */}
        {peakY !== null && (
          <>
            <line
              x1={0}
              y1={peakY}
              x2={W}
              y2={peakY}
              stroke="var(--muted-2)"
              strokeDasharray="3 3"
              strokeWidth={1}
            />
            <text x={W} y={peakY - 4} textAnchor="end" fontSize={9} fill="var(--muted-2)">
              peak {pct(historical_peak_gross_margin)}
            </text>
          </>
        )}

        {/* Headroom band between current and peak. */}
        {peakY !== null && (
          <rect
            x={0}
            y={peakY}
            width={W}
            height={Math.max(y(points[currentIdx].gross_margin) - peakY, 0)}
            fill="var(--warning)"
            opacity={0.06}
          />
        )}

        <path d={path} fill="none" stroke="var(--info)" strokeWidth={1.5} />

        {points.map((p, i) => (
          <circle
            key={p.quarter_end}
            cx={x(i)}
            cy={y(p.gross_margin)}
            r={i === currentIdx ? 3 : 1.6}
            fill={i === currentIdx ? "var(--info)" : "var(--muted-2)"}
          />
        ))}

        {/* Current-value marker + label. */}
        <text
          x={x(currentIdx)}
          y={y(points[currentIdx].gross_margin) - 8}
          textAnchor="end"
          fontSize={9}
          fill="var(--info)"
        >
          current {pct(current_gross_margin)}
        </text>
      </svg>

      <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-[var(--muted-2)] font-mono-jb">
        <span>{points[0]?.quarter_end}</span>
        <span>
          {history.quarters} quarters
          {history.dropped_implausible > 0 &&
            ` · ${history.dropped_implausible} dropped (implausible margin)`}
        </span>
        <span>{points[points.length - 1]?.quarter_end}</span>
      </div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--muted-2)]">{label}</div>
      <div
        className={`text-lg font-semibold tabular-nums ${accent ? "text-[var(--warning)]" : ""}`}
      >
        {value}
      </div>
    </div>
  );
}

function pct(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}
