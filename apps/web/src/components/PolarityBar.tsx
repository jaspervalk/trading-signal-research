import { cn } from "@/lib/utils";

/**
 * Horizontal ±1 micro-bar with a center axis and a position marker.
 * Used inside SignalTile and CreatorCoverage rows.
 *
 * value is clamped to [-1, 1]. null renders an empty axis-only track.
 */
export function PolarityBar({
  value,
  className,
}: {
  value: number | null | undefined;
  className?: string;
}) {
  const v = value === null || value === undefined ? 0 : Math.max(-1, Math.min(1, value));
  const hasValue = value !== null && value !== undefined;
  // Center is 50%. Fill grows from center toward the value's sign.
  const fillLeft = v >= 0 ? 50 : 50 + v * 50;
  const fillWidth = Math.abs(v) * 50;
  const markerLeft = 50 + v * 50;
  const fillBg =
    v > 0.001
      ? "var(--positive)"
      : v < -0.001
        ? "var(--negative)"
        : "var(--muted-foreground)";

  return (
    <div className={cn("relative h-1 w-full bg-[var(--hairline-2)]", className)}>
      <div
        className="absolute top-[-2px] bottom-[-2px] w-px bg-[var(--border)]"
        style={{ left: "50%" }}
      />
      {hasValue && (
        <div
          className="absolute top-0 bottom-0"
          style={{ left: `${fillLeft}%`, width: `${fillWidth}%`, background: fillBg }}
        />
      )}
      {hasValue && (
        <div
          className="absolute top-[-3px] w-0.5 h-2.5 bg-[var(--foreground)]"
          style={{ left: `calc(${markerLeft}% - 1px)` }}
        />
      )}
    </div>
  );
}
