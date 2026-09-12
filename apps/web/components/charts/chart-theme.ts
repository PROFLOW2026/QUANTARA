"use client";

import { useTheme } from "next-themes";
import { useMemo } from "react";

export type ChartColorPalette = {
  profit: string;
  loss: string;
  accent: string;
  muted: string;
  border: string;
  surface: string;
  grid: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
};

const DARK_CHART: ChartColorPalette = {
  profit: "#22c55e",
  loss: "#ef4444",
  accent: "#3b82f6",
  muted: "#64748b",
  border: "#1e293b",
  surface: "#111827",
  grid: "#1e293b",
  tooltipBg: "#1a2234",
  tooltipBorder: "#1e293b",
  tooltipText: "#e2e8f0",
};

const LIGHT_CHART: ChartColorPalette = {
  profit: "#187a45",
  loss: "#c0392f",
  accent: "#4a7dab",
  muted: "#5a6578",
  border: "#d5dde7",
  surface: "#f3f6fa",
  grid: "#dde4ec",
  tooltipBg: "#fcfdfe",
  tooltipBorder: "#d5dde7",
  tooltipText: "#0f172a",
};

/** @deprecated use useChartTheme() */
export const CHART_COLORS = DARK_CHART;

export function chartColorsForTheme(theme: string | undefined): ChartColorPalette {
  return theme === "light" ? LIGHT_CHART : DARK_CHART;
}

export function useChartTheme() {
  const { resolvedTheme } = useTheme();
  return useMemo(
    () => chartColorsForTheme(resolvedTheme),
    [resolvedTheme]
  );
}

export const CHART_DEFAULTS = {
  height: 240,
  margin: { top: 8, right: 8, left: 0, bottom: 0 },
} as const;

export function axisStyle(colors: ChartColorPalette) {
  return {
    tick: { fill: colors.muted, fontSize: 11 },
    axisLine: { stroke: colors.border },
    tickLine: { stroke: colors.border },
  };
}

export function gridStyle(colors: ChartColorPalette) {
  return {
    stroke: colors.grid,
    strokeDasharray: "3 3",
    vertical: false,
  };
}

export function tooltipStyle(colors: ChartColorPalette) {
  return {
    contentStyle: {
      backgroundColor: colors.tooltipBg,
      border: `1px solid ${colors.tooltipBorder}`,
      borderRadius: "6px",
      fontSize: "12px",
    },
    labelStyle: { color: colors.muted },
    itemStyle: { color: colors.tooltipText },
  };
}
