"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { t } from "@/lib/i18n";
import { formatCurrency, formatDateTime } from "@/lib/utils";
import {
  CHART_DEFAULTS,
  axisStyle,
  gridStyle,
  tooltipStyle,
  useChartTheme,
} from "./chart-theme";

export type EquityPoint = { date: string; equity: number };

interface EquityCurveChartProps {
  data: EquityPoint[];
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

export function EquityCurveChart({ data, height = CHART_DEFAULTS.height }: EquityCurveChartProps) {
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

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={CHART_DEFAULTS.margin}>
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
          tickFormatter={(v: number) =>
            new Intl.NumberFormat("he-IL", {
              notation: "compact",
              maximumFractionDigits: 1,
            }).format(v)
          }
          tick={axis.tick}
          axisLine={axis.axisLine}
          tickLine={axis.tickLine}
          width={56}
          domain={["auto", "auto"]}
        />
        <Tooltip
          contentStyle={tooltip.contentStyle}
          labelStyle={tooltip.labelStyle}
          itemStyle={tooltip.itemStyle}
          labelFormatter={(label) => formatDateTime(String(label))}
          formatter={(value: number) => [formatCurrency(value), t("charts.equity")]}
        />
        <Line
          type="monotone"
          dataKey="equity"
          stroke={colors.accent}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: colors.accent, stroke: colors.surface }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
