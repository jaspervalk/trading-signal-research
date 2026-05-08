import type { ResearchIndicators, ResearchLevels, ResearchMarketSnapshot } from "@/lib/api";
import { cn, fmtPct } from "@/lib/utils";

/**
 * Compact technicals summary — trend / momentum / volatility / liquidity /
 * relative / levels. Lives in the left rail below the Live Signal Strip.
 *
 * The grouping is intentional: each section answers one swing-trading
 * question (regime / trigger / sizing / can-I-trade-it / vs-peers / chart).
 * Some fields surfaced from already-computed `MarketSnapshotPanel` values
 * (avg $ volume, % off 52w low, 63d & 252d returns, gap) — see ADR 0008
 * recommendations + the "what's missing" memo.
 */
export function TechnicalsCard({
  indicators,
  levels,
  market,
}: {
  indicators: ResearchIndicators;
  levels: ResearchLevels;
  market: ResearchMarketSnapshot;
}) {
  return (
    <section className="bg-[var(--panel)] border border-[var(--border)] font-mono-jb">
      <header className="px-4 py-3 border-b border-[var(--hairline-2)]">
        <h2 className="text-sm tracking-tight">Technicals</h2>
        <p className="text-xs uppercase tracking-wider text-[var(--muted-foreground)] mt-1">
          {indicators.ma_alignment.replace(/_/g, " ")} · ATR {fmtPct(indicators.atr_14_pct)}
        </p>
      </header>
      <div className="p-4 space-y-3 text-xs">
        <Section title="Trend">
          <Row label="MA stack" value={indicators.ma_alignment.replace(/_/g, " ")} />
          <Row
            label="slope 50 / 21d"
            value={fmtPct(indicators.sma_50_slope_21d_pct)}
            color={signColor(indicators.sma_50_slope_21d_pct)}
          />
          <Row
            label="slope 200 / 63d"
            value={fmtPct(indicators.sma_200_slope_63d_pct)}
            color={signColor(indicators.sma_200_slope_63d_pct)}
          />
          <Row
            label="dist 50 / 200"
            value={`${fmtPct(indicators.dist_to_sma_50_pct)} / ${fmtPct(indicators.dist_to_sma_200_pct)}`}
          />
        </Section>

        <Section title="Momentum">
          <Row
            label="RSI(14)"
            value={fmtNum(indicators.rsi_14, 0)}
            color={rsiColor(indicators.rsi_14)}
          />
          <Row
            label="return 5d"
            value={fmtPct(market.return_5d)}
            color={signColor(market.return_5d)}
          />
          <Row
            label="return 21d"
            value={fmtPct(market.return_21d)}
            color={signColor(market.return_21d)}
          />
          <Row
            label="return 63d"
            value={fmtPct(market.return_63d)}
            color={signColor(market.return_63d)}
          />
          <Row
            label="return 252d"
            value={fmtPct(market.return_252d)}
            color={signColor(market.return_252d)}
          />
          <Row
            label="gap from prev"
            value={fmtPct(market.gap_from_prev_close)}
            color={signColor(market.gap_from_prev_close)}
          />
        </Section>

        <Section title="Volatility">
          <Row label="ATR(14)%" value={fmtPct(indicators.atr_14_pct)} />
          <Row label="vol ratio 20d" value={fmtNum(indicators.volume_ratio_20)} />
          <Row
            label="tight range"
            value={levels.is_in_tight_range ? "yes" : "no"}
            color={levels.is_in_tight_range ? "text-[var(--info)]" : undefined}
          />
        </Section>

        <Section title="Liquidity">
          <Row
            label="avg $ vol 20d"
            value={fmtDollarVol(market.avg_volume_20d, market.last_close)}
            color={liquidityColor(market.avg_volume_20d, market.last_close)}
          />
          <Row
            label="off 52w high"
            value={fmtPct(market.pct_off_52w_high)}
          />
          <Row
            label="off 52w low"
            value={fmtPct(market.pct_off_52w_low)}
            color={signColor(market.pct_off_52w_low)}
          />
        </Section>

        <Section title="Relative">
          <Row
            label="vs SPY 21d"
            value={fmtPct(market.excess_return_21d)}
            color={signColor(market.excess_return_21d)}
          />
          <Row
            label="vs SPY 63d"
            value={fmtPct(indicators.relative_strength_vs_spy_63d)}
            color={signColor(indicators.relative_strength_vs_spy_63d)}
          />
        </Section>

        <Section title="Levels">
          <Row label="nearest support" value={fmtNum(levels.nearest_support)} />
          <Row label="nearest resistance" value={fmtNum(levels.nearest_resistance)} />
          <Row
            label="pullback from high"
            value={fmtPct(levels.pullback_pct_from_recent_high)}
          />
          <Row
            label="breakout dist"
            value={fmtPct(levels.breakout_distance_pct)}
            color={signColor(levels.breakout_distance_pct)}
          />
        </Section>
      </div>
    </section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs uppercase tracking-[0.12em] text-[var(--muted-2)] mb-1.5">
        {title}
      </h3>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className={cn("tabular-nums", color)}>{value}</span>
    </div>
  );
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

function fmtDollarVol(
  avgShares: number | null | undefined,
  lastClose: number | null | undefined,
): string {
  if (avgShares == null || lastClose == null) return "—";
  const usd = avgShares * lastClose;
  if (usd >= 1e9) return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6) return `$${(usd / 1e6).toFixed(1)}M`;
  if (usd >= 1e3) return `$${(usd / 1e3).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

function liquidityColor(
  avgShares: number | null | undefined,
  lastClose: number | null | undefined,
): string | undefined {
  // Below $5M/day is the standard swing-trader liquidity floor (Stockbee, IBD).
  if (avgShares == null || lastClose == null) return undefined;
  const usd = avgShares * lastClose;
  if (usd < 5_000_000) return "text-[var(--negative)]";
  if (usd < 25_000_000) return "text-[var(--warning)]";
  return undefined;
}

function signColor(value: number | null | undefined): string | undefined {
  if (value === null || value === undefined) return undefined;
  if (value > 0.0005) return "text-[var(--positive)]";
  if (value < -0.0005) return "text-[var(--negative)]";
  return undefined;
}

function rsiColor(value: number | null | undefined): string | undefined {
  if (value === null || value === undefined) return undefined;
  if (value >= 70) return "text-[var(--warning)]";
  if (value <= 30) return "text-[var(--info)]";
  return undefined;
}
