"use client";

import { useMemo, useState } from "react";
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

/** Display order of the independent comparison cohorts. */
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

/** Most specific matchers first so "Rules Mode $10K" doesn't land in "Options 10K". */
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

const COLUMNS: { key: Exclude<SortKey, "order">; label: string }[] = [
  { key: "equity", label: "הון" },
  { key: "return_pct", label: "תשואה" },
  { key: "realized_pnl", label: "ממומש" },
  { key: "unrealized_pnl", label: "לא ממומש" },
  { key: "premium_collected_gross", label: "פרמיה ברוטו" },
  { key: "premium_realized_net", label: "פרמיה נטו" },
  { key: "open_liability_usd", label: "התחייבויות" },
  { key: "collateral_locked_usd", label: "בטוחות" },
  { key: "max_drawdown_pct", label: "DD מקס׳" },
  { key: "win_rate_pct", label: "Win%" },
  { key: "profit_factor", label: "PF" },
];

type Props = {
  rows: OptionsStrategyComparisonRow[];
  selectedSlug?: string | null;
  onSelectAccount?: (slug: string) => void;
};

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
    () => ordered.map((r) => ({ label: r.label, return_pct: Math.round(r.return_pct * 100) / 100 })),
    [ordered]
  );

  const toggleSort = (key: SortKey) => {
    if (sort === key) setDesc((d) => !d);
    else {
      setSort(key);
      setDesc(key !== "max_drawdown_pct");
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <CardTitle>השוואת אסטרטגיות וקבוצות ניסוי</CardTitle>
          <div className="flex items-center gap-2">
            {best ? (
              <Badge variant="success">
                מוביל: {best.label} ({formatPercent(best.return_pct)})
              </Badge>
            ) : null}
            {sort !== "order" ? (
              <button
                type="button"
                onClick={() => setSort("order")}
                className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:bg-surface-active"
              >
                סדר ברירת מחדל
              </button>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {rows.length === 0 ? (
            <EmptyState message="אין עדיין נתוני השוואה. כל קבוצה רצה כחשבון סימולציה עצמאי." />
          ) : (
            <div className="overflow-x-auto rounded-md border border-table-border scrollbar-thin">
              <table className="w-full min-w-max text-sm">
                <thead className="bg-table-header text-xs text-table-header-text">
                  <tr>
                    <th className="sticky start-0 z-10 bg-table-header px-3 py-2 text-start font-medium">קבוצה</th>
                    {COLUMNS.map((c) => (
                      <th key={c.key} className="px-3 py-2 text-start font-medium">
                        <button
                          type="button"
                          onClick={() => toggleSort(c.key)}
                          className={cn("hover:text-foreground", sort === c.key && "text-accent")}
                        >
                          {c.label}
                          {sort === c.key ? (desc ? " ↓" : " ↑") : ""}
                        </button>
                      </th>
                    ))}
                    <th className="px-3 py-2 text-start font-medium">פתוחות/סגורות</th>
                  </tr>
                </thead>
                <tbody>
                  {ordered.map((r) => {
                    const selected = selectedSlug === r.account_slug;
                    return (
                      <tr
                        key={r.cohort_key}
                        onClick={() => onSelectAccount?.(r.account_slug)}
                        className={cn(
                          "border-b border-table-border bg-table-row hover:bg-table-hover",
                          onSelectAccount && "cursor-pointer",
                          selected && "bg-surface-highlight"
                        )}
                      >
                        <td className="sticky start-0 z-10 bg-inherit px-3 py-2">
                          <div className="flex flex-col">
                            <span className="font-medium">
                              {r.label}
                              {best?.cohort_key === r.cohort_key ? " 🏆" : ""}
                            </span>
                            <span className="text-xs text-muted">
                              {r.mode === "ai" ? "AI" : "חוקים"} ·{" "}
                              {r.strategy_scope && r.strategy_scope !== "all" && r.strategy_scope !== "multi"
                                ? strategyLabel(r.strategy_scope)
                                : "רב־אסטרטגי"}{" "}
                              · {formatCurrency(r.starting_capital)}
                            </span>
                          </div>
                        </td>
                        <td className="px-3 py-2 font-mono">{formatCurrency(r.equity)}</td>
                        <td className={cn("px-3 py-2 font-mono", r.return_pct >= 0 ? "text-profit" : "text-loss")}>
                          {formatPercent(r.return_pct)}
                        </td>
                        <td className="px-3 py-2">
                          <PnLDisplay value={r.realized_pnl} size="sm" />
                        </td>
                        <td className="px-3 py-2">
                          <PnLDisplay value={r.unrealized_pnl} size="sm" />
                        </td>
                        <td className="px-3 py-2 font-mono">{formatCurrency(r.premium_collected_gross)}</td>
                        <td
                          className={cn(
                            "px-3 py-2 font-mono",
                            r.premium_realized_net >= 0 ? "text-profit" : "text-loss"
                          )}
                        >
                          {formatCurrency(r.premium_realized_net)}
                        </td>
                        <td className="px-3 py-2 font-mono text-loss">{formatCurrency(r.open_liability_usd)}</td>
                        <td className="px-3 py-2 font-mono text-warning">{formatCurrency(r.collateral_locked_usd)}</td>
                        <td className="px-3 py-2 font-mono text-loss">{formatRiskPercent(r.max_drawdown_pct)}</td>
                        <td className="px-3 py-2 font-mono">
                          {r.win_rate_pct != null ? `${r.win_rate_pct.toFixed(1)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 font-mono">
                          {r.profit_factor != null ? (r.profit_factor >= 999 ? "∞" : r.profit_factor.toFixed(2)) : "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">
                          {r.open_count} / {r.closed_count}
                        </td>
                      </tr>
                    );
                  })}
                  {missing.map((m) => (
                    <tr key={m.key} className="border-b border-table-border bg-table-row opacity-60">
                      <td className="sticky start-0 z-10 bg-inherit px-3 py-2 font-medium">{m.label}</td>
                      <td colSpan={COLUMNS.length + 1} className="px-3 py-2 text-xs text-muted">
                        אין נתונים עדיין — הקבוצה טרם הופעלה או טרם החזירה תוצאות
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-muted">
            כל קבוצה היא חשבון סימולציה נפרד עם הון ותקציב סיכון משלה — אין העברת כספים או התחשבנות בין קבוצות.
            לחיצה על שורה בוחרת את החשבון.
          </p>
        </CardContent>
      </Card>

      {chartData.length ? (
        <Card>
          <CardHeader>
            <CardTitle>תשואה לפי קבוצה</CardTitle>
          </CardHeader>
          <CardContent>
            <div dir="ltr">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid {...grid} />
                  <XAxis
                    dataKey="label"
                    tick={axis.tick}
                    axisLine={axis.axisLine}
                    tickLine={axis.tickLine}
                    interval={0}
                    angle={-20}
                    textAnchor="end"
                    height={56}
                  />
                  <YAxis
                    tickFormatter={(v: number) => `${v}%`}
                    tick={axis.tick}
                    axisLine={axis.axisLine}
                    tickLine={axis.tickLine}
                    width={48}
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
