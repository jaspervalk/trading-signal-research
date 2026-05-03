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
