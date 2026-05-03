"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
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

export function PriceChart({
  bars, calls,
}: { bars: Bar[]; calls: TickerCall[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

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
      upColor: POSITIVE, downColor: NEGATIVE,
      borderUpColor: POSITIVE, borderDownColor: NEGATIVE,
      wickUpColor: POSITIVE, wickDownColor: NEGATIVE,
    });
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series, []);
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, []);

  // Push data + markers whenever bars/calls change.
  useEffect(() => {
    const series = seriesRef.current;
    const markersApi = markersRef.current;
    if (!series || !markersApi || bars.length === 0) return;
    series.setData(
      bars.map((b) => ({
        time: b.time as Time,
        open: b.open, high: b.high, low: b.low, close: b.close,
      })),
    );

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

  return <div ref={containerRef} className="w-full h-[420px]" />;
}
