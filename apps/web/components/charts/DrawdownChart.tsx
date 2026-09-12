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
  CHART_DEFAULTS,
  axisStyle,
  gridStyle,
  tooltipStyle,
  useChartTheme,
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
  const colors = useChartTheme();
  const axis = axisStyle(colors);
  const grid = gridStyle(colors);
  const tooltip = tooltipStyle(colors);

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
            <stop offset="0%" stopColor={colors.loss} stopOpacity={0.1} />
            <stop offset="100%" stopColor={colors.loss} stopOpacity={0.45} />
          </linearGradient>
        </defs>
        <CartesianGrid {...grid} />
        <XAxis
          dataKey="date"
          tickFormatter={formatAxisDate}
          tick={axis.tick}
          axisLine={axis.axisLine}
          tickLine={axis.tickLine}
          minTickGap={32}
        />
        <YAxis
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          tick={axis.tick}
          axisLine={axis.axisLine}
          tickLine={axis.tickLine}
          width={48}
          domain={["dataMin", 0]}
        />
        <Tooltip
          contentStyle={tooltip.contentStyle}
          labelStyle={tooltip.labelStyle}
          itemStyle={tooltip.itemStyle}
          labelFormatter={(label) => formatDateTime(String(label))}
          formatter={(value: number) => [
            formatPercent(value),
            t("charts.drawdown"),
          ]}
        />
        <Area
          type="monotone"
          dataKey="drawdown_display"
          stroke={colors.loss}
          strokeWidth={1.5}
          fill="url(#drawdownGradient)"
          dot={false}
          activeDot={{ r: 4, fill: colors.loss, stroke: colors.surface }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
