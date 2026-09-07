"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { t } from "@/lib/i18n";
import { formatCurrency, formatDateTime } from "@/lib/utils";
import {
  CHART_DEFAULTS,
  axisStyle,
  gridStyle,
  tooltipStyle,
} from "./chart-theme";

const LINE_COLORS = ["#3b82f6", "#22c55e", "#eab308", "#f97316", "#ef4444"];

export type MultiEquitySeries = {
  id: string;
  name: string;
  data: Array<{ date: string; equity: number }>;
};

interface MultiEquityCurveChartProps {
  series: MultiEquitySeries[];
  height?: number;
}

function formatAxisDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("he-IL", {
      month: "short",
      day: "numeric",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function mergeSeries(series: MultiEquitySeries[]) {
  const map = new Map<string, Record<string, number | string>>();
  for (const s of series) {
    for (const point of s.data) {
      const row = map.get(point.date) ?? { date: point.date };
      row[s.id] = point.equity;
      map.set(point.date, row);
    }
  }
  return Array.from(map.values()).sort((a, b) =>
    String(a.date).localeCompare(String(b.date))
  );
}

export function MultiEquityCurveChart({
  series,
  height = 280,
}: MultiEquityCurveChartProps) {
  if (!series.length || series.every((s) => !s.data.length)) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-border bg-surface-elevated/30"
        style={{ height }}
      >
        <p className="text-sm text-muted">{t("charts.no_data")}</p>
      </div>
    );
  }

  const data = mergeSeries(series);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={CHART_DEFAULTS.margin}>
        <CartesianGrid {...gridStyle} />
        <XAxis
          dataKey="date"
          tickFormatter={formatAxisDate}
          tick={axisStyle.tick}
          axisLine={axisStyle.axisLine}
          tickLine={axisStyle.tickLine}
          minTickGap={32}
        />
        <YAxis
          tickFormatter={(v: number) =>
            new Intl.NumberFormat("he-IL", {
              notation: "compact",
              maximumFractionDigits: 1,
            }).format(v)
          }
          tick={axisStyle.tick}
          axisLine={axisStyle.axisLine}
          tickLine={axisStyle.tickLine}
          width={56}
          domain={["auto", "auto"]}
        />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(label) => formatDateTime(String(label))}
          formatter={(value: number, name: string) => {
            const label = series.find((s) => s.id === name)?.name ?? name;
            return [formatCurrency(value), label];
          }}
        />
        <Legend />
        {series.map((s, i) => (
          <Line
            key={s.id}
            type="monotone"
            dataKey={s.id}
            name={s.name}
            stroke={LINE_COLORS[i % LINE_COLORS.length]}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
