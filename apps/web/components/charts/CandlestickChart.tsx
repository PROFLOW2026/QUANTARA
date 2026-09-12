"use client";

import { useCallback, useMemo } from "react";
import {
  ComposedChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Bar,
  Customized,
} from "recharts";
import type { Candle } from "@/lib/api-client";
import type { ChartExecutionMarker } from "./chart-types";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPrice } from "@/lib/utils";
import {
  CHART_COLORS,
  CHART_DEFAULTS,
  axisStyle,
  gridStyle,
  tooltipStyle,
} from "./chart-theme";

interface CandlestickChartProps {
  candles: Candle[];
  height?: number;
  timeframe?: string;
  /** Reserved for future fill-based execution markers. */
  markers?: ChartExecutionMarker[];
}

interface CandleChartPoint {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  isUp: boolean;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function buildDayBoundaryTimes(times: string[]): Set<string> {
  const boundaries = new Set<string>();
  let lastDay: string | null = null;
  for (const time of times) {
    const currentDay = dayKey(time);
    if (currentDay !== lastDay) {
      boundaries.add(time);
      lastDay = currentDay;
    }
  }
  return boundaries;
}

function formatChartAxisTick(
  iso: string,
  timeframe: string,
  dayBoundaries: Set<string>
): string {
  const d = new Date(iso);
  const dd = pad2(d.getDate());
  const mm = pad2(d.getMonth() + 1);
  const hh = pad2(d.getHours());
  const min = pad2(d.getMinutes());

  if (timeframe === "1h") {
    return `${dd}/${mm} ${hh}:00`;
  }

  if (dayBoundaries.has(iso)) {
    return `${dd}/${mm} ${hh}:${min}`;
  }

  return `${hh}:${min}`;
}

/** Moderate body width for ~100–120 candle windows with visible gaps. */
function resolveCandleBodyWidth(bandwidth: number, candleCount: number): number {
  const bodyFillRatio =
    candleCount >= 140
      ? 0.38
      : candleCount >= 100
        ? 0.42
        : candleCount >= 60
          ? 0.44
          : 0.48;
  const minBody = 3;
  const maxBody = bandwidth * 0.5;
  return Math.min(Math.max(bandwidth * bodyFillRatio, minBody), maxBody);
}

function CandlestickLayer(props: {
  xAxisMap?: Record<string, { scale: (v: string) => number; bandwidth?: () => number }>;
  yAxisMap?: Record<string, { scale: (v: number) => number }>;
  data?: CandleChartPoint[];
}) {
  const { xAxisMap, yAxisMap, data } = props;
  if (!xAxisMap || !yAxisMap || !data?.length) return null;

  const xAxis = Object.values(xAxisMap)[0];
  const yAxis = Object.values(yAxisMap)[0];
  const bandwidth = xAxis.bandwidth?.() ?? 12;
  const bodyWidth = resolveCandleBodyWidth(bandwidth, data.length);

  return (
    <g>
      {data.map((c) => {
        const xCenter = xAxis.scale(c.time) + bandwidth / 2;
        const yHigh = yAxis.scale(c.high);
        const yLow = yAxis.scale(c.low);
        const yOpen = yAxis.scale(c.open);
        const yClose = yAxis.scale(c.close);
        const color = c.isUp ? CHART_COLORS.profit : CHART_COLORS.loss;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyBottom = Math.max(yOpen, yClose);
        const bodyHeight = Math.max(bodyBottom - bodyTop, 1);

        return (
          <g key={c.time}>
            <line
              x1={xCenter}
              y1={yHigh}
              x2={xCenter}
              y2={yLow}
              stroke={color}
              strokeWidth={1}
            />
            <rect
              x={xCenter - bodyWidth / 2}
              y={bodyTop}
              width={bodyWidth}
              height={bodyHeight}
              fill={color}
              stroke={color}
              strokeWidth={0.5}
            />
          </g>
        );
      })}
    </g>
  );
}

export function CandlestickChart({
  candles,
  height = 280,
  timeframe = "15m",
  markers: _markers,
}: CandlestickChartProps) {
  const chartData = useMemo<CandleChartPoint[]>(
    () =>
      [...candles]
        .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
        .map((c) => ({
          time: c.time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          isUp: c.close >= c.open,
        })),
    [candles]
  );

  const dayBoundaries = useMemo(
    () => buildDayBoundaryTimes(chartData.map((c) => c.time)),
    [chartData]
  );

  const tickFormatter = useCallback(
    (iso: string) => formatChartAxisTick(iso, timeframe, dayBoundaries),
    [timeframe, dayBoundaries]
  );

  if (!chartData.length) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-border bg-surface-elevated/30" style={{ height }}>
        <p className="text-sm text-muted">{t("charts.no_data")}</p>
      </div>
    );
  }

  const yDomain = [
    Math.min(...chartData.map((c) => c.low)) * 0.9995,
    Math.max(...chartData.map((c) => c.high)) * 1.0005,
  ] as [number, number];

  const minTickGap = timeframe === "1h" ? 48 : timeframe === "15m" ? 44 : 36;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={chartData}
        margin={{ ...CHART_DEFAULTS.margin, bottom: 4 }}
        barCategoryGap="24%"
      >
        <CartesianGrid {...gridStyle} />
        <XAxis
          dataKey="time"
          tickFormatter={tickFormatter}
          tick={axisStyle.tick}
          axisLine={axisStyle.axisLine}
          tickLine={axisStyle.tickLine}
          minTickGap={minTickGap}
        />
        <YAxis
          domain={yDomain}
          tickFormatter={(v: number) => formatPrice(v)}
          tick={axisStyle.tick}
          axisLine={axisStyle.axisLine}
          tickLine={axisStyle.tickLine}
          width={72}
        />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(label) => formatDateTime(String(label))}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const c = payload[0].payload as CandleChartPoint;
            return (
              <div
                style={{
                  ...tooltipStyle.contentStyle,
                  padding: "8px 12px",
                }}
              >
                <p style={{ ...tooltipStyle.labelStyle, marginBottom: 4 }}>
                  {formatDateTime(c.time)}
                </p>
                <p style={tooltipStyle.itemStyle}>
                  {t("market.open")}: {formatPrice(c.open)}
                </p>
                <p style={tooltipStyle.itemStyle}>
                  {t("market.high")}: {formatPrice(c.high)}
                </p>
                <p style={tooltipStyle.itemStyle}>
                  {t("market.low")}: {formatPrice(c.low)}
                </p>
                <p style={{ ...tooltipStyle.itemStyle, color: c.isUp ? CHART_COLORS.profit : CHART_COLORS.loss }}>
                  {t("market.close")}: {formatPrice(c.close)}
                </p>
              </div>
            );
          }}
        />
        {/* Invisible bar anchors the x-axis scale for Customized candle shapes */}
        <Bar dataKey="close" fill="transparent" stroke="transparent" />
        <Customized
          component={(props: {
            xAxisMap?: Record<string, { scale: (v: string) => number; bandwidth?: () => number }>;
            yAxisMap?: Record<string, { scale: (v: number) => number }>;
          }) => <CandlestickLayer {...props} data={chartData} />}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
