"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar, TickerCall } from "@/lib/api";

const POSITIVE = "#22c55e";
const NEGATIVE = "#ef4444";
const NEUTRAL = "#a1a1aa";
const SMA_50 = "#60a5fa";
const SMA_200 = "#a78bfa";
const INFO = "#22d3ee";

/** Simple rolling mean, aligned to the right edge of each window. */
function sma(bars: Bar[], window: number): { time: Time; value: number }[] {
  if (bars.length < window) return [];
  const out: { time: Time; value: number }[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= window) sum -= bars[i - window].close;
    if (i >= window - 1) {
      out.push({ time: bars[i].time as Time, value: sum / window });
    }
  }
  return out;
}

/**
 * Price chart with the overlays that carry decision weight.
 *
 * Four lines, deliberately. The 50 and 200 day averages are the substrate the
 * decision rubric already reasons over (`ma_alignment`, `dist_to_sma_200_pct`),
 * so drawing them makes the verdict legible rather than asserted. Average cost
 * and the invalidation level are horizontal price lines because their whole
 * value is spatial: how far is price from where I bought, and from where the
 * thesis breaks.
 *
 * Everything here is arithmetic over the same bars the chart draws. There is no
 * valuation line: this codebase has no model that converts fundamentals into a
 * price level, and a drawn line implies a precision that would not exist.
 */
export function PriceChart({
  bars,
  calls,
  avgCost = null,
  invalidation = null,
}: {
  bars: Bar[];
  calls: TickerCall[];
  /** Your average cost, when the ticker is held. */
  avgCost?: number | null;
  /** Deterministic invalidation reference from the research view. */
  invalidation?: number | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const sma50Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const sma200Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#a1a1aa",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      },
      grid: {
        vertLines: { color: "#262626" },
        horzLines: { color: "#262626" },
      },
      rightPriceScale: { borderColor: "#262626" },
      timeScale: { borderColor: "#262626", timeVisible: false },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: POSITIVE,
      downColor: NEGATIVE,
      borderUpColor: POSITIVE,
      borderDownColor: NEGATIVE,
      wickUpColor: POSITIVE,
      wickDownColor: NEGATIVE,
    });
    seriesRef.current = series;

    // Thin and unobtrusive: the candles are the subject, the averages are context.
    sma200Ref.current = chart.addSeries(LineSeries, {
      color: SMA_200,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    sma50Ref.current = chart.addSeries(LineSeries, {
      color: SMA_50,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    markersRef.current = createSeriesMarkers(series, []);
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      sma50Ref.current = null;
      sma200Ref.current = null;
      markersRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  // Bars, averages and call markers.
  useEffect(() => {
    const series = seriesRef.current;
    const markersApi = markersRef.current;
    if (!series || !markersApi || bars.length === 0) return;

    series.setData(
      bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    // A short series yields an empty array rather than a misleading stub line.
    sma50Ref.current?.setData(sma(bars, 50));
    sma200Ref.current?.setData(sma(bars, 200));

    const markers: SeriesMarker<Time>[] = calls.map((c) => {
      const ret = c.return_5d;
      const color =
        ret === null || ret === undefined
          ? NEUTRAL
          : ret > 0.001
            ? POSITIVE
            : ret < -0.001
              ? NEGATIVE
              : NEUTRAL;
      return {
        time: c.time as Time,
        position: c.direction === "short" ? "aboveBar" : "belowBar",
        color,
        shape: c.direction === "short" ? "arrowDown" : "arrowUp",
        text: `${c.creator_name.slice(0, 12)} ${c.direction[0].toUpperCase()}${c.entry_price ? ` @${c.entry_price}` : ""}`,
      };
    });
    markersApi.setMarkers(markers);
    chartRef.current?.timeScale().fitContent();
  }, [bars, calls]);

  // Horizontal levels, rebuilt whenever they change.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = [];

    if (avgCost !== null && avgCost > 0) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: avgCost,
          color: INFO,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "your cost",
        }),
      );
    }
    if (invalidation !== null && invalidation > 0) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: invalidation,
          color: NEGATIVE,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "invalidation",
        }),
      );
    }
  }, [avgCost, invalidation]);

  return (
    <div className="space-y-2">
      <div ref={containerRef} className="w-full h-[420px]" />
      <ChartLegend
        hasSma50={bars.length >= 50}
        hasSma200={bars.length >= 200}
        hasCost={avgCost !== null && avgCost > 0}
        hasInvalidation={invalidation !== null && invalidation > 0}
      />
    </div>
  );
}

function ChartLegend({
  hasSma50,
  hasSma200,
  hasCost,
  hasInvalidation,
}: {
  hasSma50: boolean;
  hasSma200: boolean;
  hasCost: boolean;
  hasInvalidation: boolean;
}) {
  return (
    <div className="flex items-center gap-4 flex-wrap px-1 text-[10px] uppercase tracking-wider text-[var(--muted-foreground)]">
      {hasSma50 && <Swatch color={SMA_50} label="50d avg" />}
      {hasSma200 && <Swatch color={SMA_200} label="200d avg" />}
      {hasCost && <Swatch color={INFO} label="your cost" dashed />}
      {hasInvalidation && <Swatch color={NEGATIVE} label="invalidation" dashed />}
      {!hasSma200 && (
        <span className="text-[var(--muted-2)]">200d avg needs 200 bars</span>
      )}
    </div>
  );
}

function Swatch({
  color,
  label,
  dashed = false,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block w-4"
        style={{
          borderTopWidth: 1,
          borderTopStyle: dashed ? "dashed" : "solid",
          borderTopColor: color,
        }}
      />
      {label}
    </span>
  );
}
