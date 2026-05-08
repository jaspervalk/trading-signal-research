import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(digits)}%`;
}

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

export function fmtDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function fmtDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function pctColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-[var(--muted-foreground)]";
  if (value > 0.001) return "text-[var(--positive)]";
  if (value < -0.001) return "text-[var(--negative)]";
  return "text-[var(--muted-foreground)]";
}

export function statusColor(status: string): string {
  switch (status) {
    case "accepted":
    case "confirmed":
    case "evaluated":
      return "bg-[var(--positive)]/10 text-[var(--positive)] border-[var(--positive)]/20";
    case "rejected":
      return "bg-[var(--negative)]/10 text-[var(--negative)] border-[var(--negative)]/20";
    case "pending_review":
    case "flagged":
    case "data_missing":
      return "bg-[var(--warning)]/10 text-[var(--warning)] border-[var(--warning)]/20";
    default:
      return "bg-[var(--muted)] text-[var(--muted-foreground)] border-[var(--border)]";
  }
}

/** Format a Wilson 95% CI lower-bound as a bracketed value: `0.51 [≥0.51]`. */
export function fmtCiLower(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)} [≥${value.toFixed(digits)}]`;
}

/** Format a signed number with a leading + for non-negative: `+0.62`, `-0.30`, `0.00`. */
export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "" : "+";
  return `${sign}${value.toFixed(digits)}`;
}

/** Polarity → ▲/▼/◆ glyph; used in the claim row collapsed state. */
export function polarityGlyph(polarity: "bullish" | "bearish" | "neutral" | "mixed"): string {
  if (polarity === "bullish") return "▲";
  if (polarity === "bearish") return "▼";
  return "◆";
}

/** Polarity → color class. */
export function polarityColor(polarity: "bullish" | "bearish" | "neutral" | "mixed"): string {
  if (polarity === "bullish") return "text-[var(--positive)]";
  if (polarity === "bearish") return "text-[var(--negative)]";
  return "text-[var(--muted-foreground)]";
}

/** Format a date as `2026-04-30` (ISO date, mono-friendly). */
export function fmtIsoDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  return d.toISOString().slice(0, 10);
}

/** Convert seconds since the start of a video into `H:MM:SS` or `M:SS`. */
export function fmtVideoTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

/** Days since an ISO datetime (used for "historical only" >90d gate). */
export function daysSince(value: string | Date | null | undefined): number | null {
  if (!value) return null;
  const d = typeof value === "string" ? new Date(value) : value;
  const ms = Date.now() - d.getTime();
  return Math.floor(ms / (24 * 60 * 60 * 1000));
}
