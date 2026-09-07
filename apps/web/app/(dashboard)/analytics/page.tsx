"use client";

import { useEffect, useState } from "react";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { EquityCurveChart } from "@/components/charts/EquityCurveChart";
import { DrawdownChart } from "@/components/charts/DrawdownChart";
import { MetricCard, MetricCardCurrency } from "@/components/trading/MetricCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  api,
  ApiError,
  type AnalyticsPortfolio,
  type AnalyticsStrategy,
  type AnalyticsCosts,
} from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";

export default function AnalyticsPage() {
  const [tab, setTab] = useState("portfolio");
  const [portfolio, setPortfolio] = useState<AnalyticsPortfolio | null>(null);
  const [strategy, setStrategy] = useState<AnalyticsStrategy | null>(null);
  const [costs, setCosts] = useState<AnalyticsCosts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (tab === "portfolio") {
          setPortfolio(await api.getAnalyticsPortfolio());
        } else if (tab === "strategy") {
          setStrategy(await api.getAnalyticsStrategy());
        } else {
          setCosts(await api.getAnalyticsCosts());
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.message : t("common.error"));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [tab]);

  const tabs = [
    { id: "portfolio", label: t("analytics.tab_portfolio") },
    { id: "strategy", label: t("analytics.tab_strategy") },
    { id: "costs", label: t("analytics.tab_costs") },
  ];

  return (
    <>
      <PageHeader titleKey="analytics.title" />
      <Tabs tabs={tabs} active={tab} onChange={setTab} className="mb-6" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {loading ? (
        <p className="text-muted">{t("common.loading")}</p>
      ) : tab === "portfolio" ? (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label={t("analytics.total_return")}
              value={portfolio?.summary.total_return_pct != null
                ? formatPercent(portfolio.summary.total_return_pct)
                : "—"}
            />
            <MetricCard
              label={t("analytics.sharpe")}
              value={portfolio?.summary.sharpe_ratio?.toFixed(2) ?? "—"}
            />
            <MetricCard
              label={t("analytics.max_dd")}
              value={portfolio?.summary.max_drawdown_pct != null
                ? formatPercent(-portfolio.summary.max_drawdown_pct)
                : "—"}
            />
            <MetricCard
              label={t("analytics.win_rate")}
              value={portfolio?.summary.win_rate != null
                ? `${portfolio.summary.win_rate.toFixed(1)}%`
                : "—"}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>{t("analytics.equity_curve")}</CardTitle></CardHeader>
              <CardContent>
                <EquityCurveChart data={portfolio?.equity_curve ?? []} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("analytics.drawdown")}</CardTitle></CardHeader>
              <CardContent>
                <DrawdownChart data={portfolio?.drawdown_curve ?? []} />
              </CardContent>
            </Card>
          </div>
          {portfolio?.monthly_returns?.length ? (
            <Card>
              <CardHeader><CardTitle>{t("analytics.monthly_returns")}</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("common.date")}</TableHead>
                      <TableHead>{t("backtests.return")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {portfolio.monthly_returns.map((m) => (
                      <TableRow key={m.month}>
                        <TableCell>{m.month}</TableCell>
                        <TableCell className="font-mono">{formatPercent(m.return_pct)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : tab === "strategy" ? (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label={t("analytics.win_rate")} value={strategy?.win_rate != null ? `${strategy.win_rate.toFixed(1)}%` : "—"} />
            <MetricCard label={t("analytics.profit_factor")} value={strategy?.profit_factor?.toFixed(2) ?? "—"} />
            <MetricCard label={t("analytics.expectancy")} value={strategy?.expectancy?.toFixed(2) ?? "—"} />
            <MetricCard label={t("analytics.avg_holding")} value={strategy?.avg_holding_time ?? "—"} />
          </div>
          <Card>
            <CardHeader><CardTitle>{t("analytics.long_vs_short")}</CardTitle></CardHeader>
            <CardContent className="flex gap-8">
              <div>
                <p className="text-sm text-muted">Long</p>
                <p className="font-mono text-profit">{formatCurrency(strategy?.long_pnl ?? 0)}</p>
              </div>
              <div>
                <p className="text-sm text-muted">Short</p>
                <p className="font-mono text-loss">{formatCurrency(strategy?.short_pnl ?? 0)}</p>
              </div>
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCardCurrency label={t("analytics.total_fees")} value={costs?.total_fees ?? 0} />
          <MetricCardCurrency label={t("analytics.total_slippage")} value={costs?.total_slippage ?? 0} />
          <MetricCardCurrency label={t("analytics.spread_impact")} value={costs?.spread_impact ?? 0} />
        </div>
      )}
    </>
  );
}
