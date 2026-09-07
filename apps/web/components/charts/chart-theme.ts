/** Shared Recharts theme aligned with tailwind.config.ts dark palette */
export const CHART_COLORS = {
  profit: "#22c55e",
  loss: "#ef4444",
  accent: "#3b82f6",
  muted: "#64748b",
  border: "#1e293b",
  surface: "#111827",
  grid: "#1e293b",
  tooltipBg: "#1a2234",
  tooltipBorder: "#1e293b",
} as const;

export const CHART_DEFAULTS = {
  height: 240,
  margin: { top: 8, right: 8, left: 0, bottom: 0 },
} as const;

export const axisStyle = {
  tick: { fill: CHART_COLORS.muted, fontSize: 11 },
  axisLine: { stroke: CHART_COLORS.border },
  tickLine: { stroke: CHART_COLORS.border },
};

export const gridStyle = {
  stroke: CHART_COLORS.grid,
  strokeDasharray: "3 3",
  vertical: false,
};

export const tooltipStyle = {
  contentStyle: {
    backgroundColor: CHART_COLORS.tooltipBg,
    border: `1px solid ${CHART_COLORS.tooltipBorder}`,
    borderRadius: "6px",
    fontSize: "12px",
  },
  labelStyle: { color: CHART_COLORS.muted },
  itemStyle: { color: "#e2e8f0" },
};
