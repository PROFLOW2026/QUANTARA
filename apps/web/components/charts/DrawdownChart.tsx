"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPercent } from "@/lib/utils";
import {
  CHART_COLORS,
  CHART_DEFAULTS,
  axisStyle,
  gridStyle,
  tooltipStyle,
} from "./chart-theme";

export type DrawdownPoint = { date: string; drawdown_pct: number };

interface DrawdownChartProps {
  data: DrawdownPoint[];
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

export function DrawdownChart({ data, height = CHART_DEFAULTS.height }: DrawdownChartProps) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-border bg-surface-elevated/30" style={{ height }}>
        <p className="text-sm text-muted">{t("charts.no_data")}</p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    drawdown_display: -Math.abs(d.drawdown_pct),
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chartData} margin={CHART_DEFAULTS.margin}>
        <defs>
          <linearGradient id="drawdownGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_COLORS.loss} stopOpacity={0.1} />
            <stop offset="100%" stopColor={CHART_COLORS.loss} stopOpacity={0.45} />
          </linearGradient>
        </defs>
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
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          tick={axisStyle.tick}
          axisLine={axisStyle.axisLine}
          tickLine={axisStyle.tickLine}
          width={48}
          domain={["dataMin", 0]}
        />
        <Tooltip
          {...tooltipStyle}
          labelFormatter={(label) => formatDateTime(String(label))}
          formatter={(value: number) => [
            formatPercent(value),
            t("charts.drawdown"),
          ]}
        />
        <Area
          type="monotone"
          dataKey="drawdown_display"
          stroke={CHART_COLORS.loss}
          strokeWidth={1.5}
          fill="url(#drawdownGradient)"
          dot={false}
          activeDot={{ r: 4, fill: CHART_COLORS.loss, stroke: CHART_COLORS.surface }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
