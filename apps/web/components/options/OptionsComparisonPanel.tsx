"use client";

import { useMemo, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { axisStyle, gridStyle, tooltipStyle, useChartTheme } from "@/components/charts/chart-theme";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/table";
import type { OptionsStrategyComparisonRow } from "@/lib/options-types";
import { strategyLabel } from "@/lib/options-types";
import { cn, formatCurrency, formatPercent, formatRiskPercent } from "@/lib/utils";

type ExpectedCohort = { key: string; label: string; match: RegExp };

const EXPECTED_COHORTS: ExpectedCohort[] = [
  { key: "options_5k", label: "Options 5K (AI)", match: /(^|[^0-9])5k|(^|[^0-9])5000/ },
  { key: "options_10k", label: "Options 10K (AI)", match: /^options-10k$|\b10k\b/ },
  { key: "options_25k", label: "Options 25K (AI)", match: /(^|[^0-9])25k|(^|[^0-9])25000/ },
  { key: "rules_mode", label: "Rules Mode $10K", match: /rules/ },
  { key: "long_only", label: "Long Only $10K", match: /long[_\s-]?only/ },
  { key: "credit_spreads", label: "Credit Spreads $10K", match: /credit/ },
  { key: "wheel", label: "Wheel $10K", match: /wheel/ },
  { key: "iron_condor", label: "Iron Condor $10K", match: /iron|condor/ },
];

const MATCH_PRIORITY = [
  "rules_mode",
  "long_only",
  "credit_spreads",
  "wheel",
  "iron_condor",
  "options_25k",
  "options_10k",
  "options_5k",
];

function cohortOf(row: OptionsStrategyComparisonRow): string | null {
  const hay = `${row.cohort_key} ${row.label}`.toLowerCase();
  for (const key of MATCH_PRIORITY) {
    const c = EXPECTED_COHORTS.find((e) => e.key === key);
    if (c && c.match.test(hay)) return key;
  }
  return null;
}

type SortKey =
  | "order"
  | "equity"
  | "return_pct"
  | "realized_pnl"
  | "unrealized_pnl"
  | "premium_collected_gross"
  | "premium_realized_net"
  | "open_liability_usd"
  | "collateral_locked_usd"
  | "max_drawdown_pct"
  | "win_rate_pct"
  | "profit_factor";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "order", label: "סדר ברירת מחדל" },
  { key: "return_pct", label: "תשואה" },
  { key: "equity", label: "הון" },
  { key: "realized_pnl", label: "ממומש" },
  { key: "max_drawdown_pct", label: "DD מקס׳" },
  { key: "profit_factor", label: "PF" },
  { key: "win_rate_pct", label: "Win%" },
];

type Props = {
  rows: OptionsStrategyComparisonRow[];
  selectedSlug?: string | null;
  onSelectAccount?: (slug: string) => void;
};

function Metric({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p className="text-[11px] text-muted">{label}</p>
      <div className="mt-0.5 font-mono text-sm tabular-nums">{children}</div>
    </div>
  );
}

export function OptionsComparisonPanel({ rows, selectedSlug, onSelectAccount }: Props) {
  const colors = useChartTheme();
  const axis = axisStyle(colors);
  const grid = gridStyle(colors);
  const tooltip = tooltipStyle(colors);
  const [sort, setSort] = useState<SortKey>("order");
  const [desc, setDesc] = useState(true);

  const { ordered, missing } = useMemo(() => {
    const orderIdx = (r: OptionsStrategyComparisonRow) => {
      const c = cohortOf(r);
      const i = EXPECTED_COHORTS.findIndex((e) => e.key === c);
      return i === -1 ? EXPECTED_COHORTS.length : i;
    };
    const matched = new Set(rows.map(cohortOf).filter(Boolean));
    const value = (r: OptionsStrategyComparisonRow): number => {
      if (sort === "order") return -orderIdx(r);
      const v = r[sort];
      return v == null ? -Infinity : v;
    };
    const list = [...rows].sort((a, b) => {
      const diff = value(b) - value(a);
      return sort === "order" || desc ? diff : -diff;
    });
    return {
      ordered: list,
      missing: EXPECTED_COHORTS.filter((e) => !matched.has(e.key)),
    };
  }, [rows, sort, desc]);

  const best = useMemo(() => {
    if (!rows.length) return null;
    return rows.reduce((b, r) => (r.return_pct > b.return_pct ? r : b));
  }, [rows]);

  const chartData = useMemo(
    () =>
      ordered.map((r) => ({
        label: r.label.length > 18 ? `${r.label.slice(0, 16)}…` : r.label,
        return_pct: Math.round(r.return_pct * 100) / 100,
      })),
    [ordered]
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <CardTitle>השוואת אסטרטגיות וקבוצות ניסוי</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            {best ? (
              <Badge variant="success">
                מוביל: {best.label} ({formatPercent(best.return_pct)})
              </Badge>
            ) : null}
            <label className="flex items-center gap-2 text-xs text-muted">
              מיון
              <select
                value={sort}
                onChange={(e) => {
                  const key = e.target.value as SortKey;
                  setSort(key);
                  setDesc(key !== "max_drawdown_pct");
                }}
                className="rounded-md border border-input-border bg-surface-inner px-2 py-1 text-sm text-foreground"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.key} value={o.key}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            {sort !== "order" ? (
              <button
                type="button"
                onClick={() => setDesc((d) => !d)}
                className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:bg-surface-active"
              >
                {desc ? "יורד ↓" : "עולה ↑"}
              </button>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {rows.length === 0 ? (
            <EmptyState message="אין עדיין נתוני השוואה. כל קבוצה רצה כחשבון סימולציה עצמאי." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {ordered.map((r) => {
                const selected = selectedSlug === r.account_slug;
                const isBest = best?.cohort_key === r.cohort_key;
                return (
                  <button
                    key={r.cohort_key}
                    type="button"
                    onClick={() => onSelectAccount?.(r.account_slug)}
                    className={cn(
                      "min-w-0 rounded-lg border p-3 text-start transition-colors",
                      selected
                        ? "border-primary bg-primary-soft ring-1 ring-primary"
                        : "border-border bg-surface hover:bg-surface-active",
                      onSelectAccount && "cursor-pointer"
                    )}
                  >
                    <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-foreground">
                          {r.label}
                          {isBest ? " ★" : ""}
                        </p>
                        <p className="text-xs text-muted">
                          {r.mode === "ai" ? "AI" : "חוקים"} ·{" "}
                          {r.strategy_scope && r.strategy_scope !== "all" && r.strategy_scope !== "multi"
                            ? strategyLabel(r.strategy_scope)
                            : "רב־אסטרטגי"}{" "}
                          · {formatCurrency(r.starting_capital)}
                        </p>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 font-mono text-sm font-semibold tabular-nums",
                          r.return_pct >= 0 ? "text-profit" : "text-loss"
                        )}
                      >
                        {formatPercent(r.return_pct)}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      <Metric label="הון">{formatCurrency(r.equity)}</Metric>
                      <Metric label="ממומש">
                        <PnLDisplay value={r.realized_pnl} size="sm" />
                      </Metric>
                      <Metric label="לא ממומש">
                        <PnLDisplay value={r.unrealized_pnl} size="sm" />
                      </Metric>
                      <Metric label="פרמיה נטו">
                        <span className={r.premium_realized_net >= 0 ? "text-profit" : "text-loss"}>
                          {formatCurrency(r.premium_realized_net)}
                        </span>
                      </Metric>
                      <Metric label="התחייבויות">
                        <span className="text-loss">{formatCurrency(r.open_liability_usd)}</span>
                      </Metric>
                      <Metric label="בטוחות">
                        <span className="text-warning">{formatCurrency(r.collateral_locked_usd)}</span>
                      </Metric>
                      <Metric label="DD מקס׳">
                        <span className="text-loss">{formatRiskPercent(r.max_drawdown_pct)}</span>
                      </Metric>
                      <Metric label="Win% / PF">
                        {r.win_rate_pct != null ? `${r.win_rate_pct.toFixed(1)}%` : "—"} /{" "}
                        {r.profit_factor != null ? (r.profit_factor >= 999 ? "∞" : r.profit_factor.toFixed(2)) : "—"}
                      </Metric>
                      <Metric label="פתוחות/סגורות">
                        {r.open_count} / {r.closed_count}
                      </Metric>
                    </div>
                  </button>
                );
              })}
              {missing.map((m) => (
                <div
                  key={m.key}
                  className="min-w-0 rounded-lg border border-dashed border-border bg-surface-inner p-3 opacity-70"
                >
                  <p className="text-sm font-medium text-foreground">{m.label}</p>
                  <p className="mt-1 text-xs text-muted">אין נתונים עדיין — הקבוצה טרם הופעלה</p>
                </div>
              ))}
            </div>
          )}
          <p className="text-xs text-muted">
            כל קבוצה היא חשבון סימולציה נפרד עם הון ותקציב סיכון משלה — אין העברת כספים בין קבוצות. לחיצה בוחרת
            חשבון.
          </p>
        </CardContent>
      </Card>

      {chartData.length ? (
        <Card>
          <CardHeader>
            <CardTitle>תשואה לפי קבוצה</CardTitle>
          </CardHeader>
          <CardContent>
            <div dir="ltr" className="w-full">
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 48 }}>
                  <CartesianGrid {...grid} />
                  <XAxis
                    dataKey="label"
                    tick={axis.tick}
                    axisLine={axis.axisLine}
                    tickLine={axis.tickLine}
                    interval={0}
                    angle={-25}
                    textAnchor="end"
                    height={64}
                  />
                  <YAxis
                    tickFormatter={(v: number) => `${v}%`}
                    tick={axis.tick}
                    axisLine={axis.axisLine}
                    tickLine={axis.tickLine}
                    width={44}
                  />
                  <Tooltip
                    contentStyle={tooltip.contentStyle}
                    labelStyle={tooltip.labelStyle}
                    itemStyle={tooltip.itemStyle}
                    formatter={(value: number) => [formatPercent(value), "תשואה"]}
                  />
                  <ReferenceLine y={0} stroke={colors.muted} />
                  <Bar dataKey="return_pct" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                    {chartData.map((d) => (
                      <Cell key={d.label} fill={d.return_pct >= 0 ? colors.profit : colors.loss} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
