"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { MultiEquityCurveChart } from "@/components/charts/MultiEquityCurveChart";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  api,
  ApiError,
  type CompetitionResponse,
} from "@/lib/api-client";
import { translateRiskProfile } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import {
  formatCurrency,
  formatDateTime,
  formatPercent,
  formatRelativeTime,
} from "@/lib/utils";

const POLL_INTERVAL = 60_000;

export default function PortfolioComparisonPage() {
  const [data, setData] = useState<CompetitionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.getCompetition();
      setData(res);
      setError(res.active ? null : t("common.no_data"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("common.error"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchData]);

  if (loading) {
    return (
      <>
        <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />
        <p className="text-muted">{t("common.loading")}</p>
      </>
    );
  }

  if (error || !data?.active || !data.experiment) {
    return (
      <>
        <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />
        <ErrorBanner message={error ?? t("common.no_data")} />
      </>
    );
  }

  const chartSeries =
    data.portfolios?.map((p) => ({
      id: p.id,
      name: p.name,
      data: data.equity_curves?.[p.id] ?? [],
    })) ?? [];

  return (
    <>
      <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />

      <Card className="mb-4">
        <CardContent className="grid gap-3 pt-6 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-muted">{t("competition.experiment_start")}</p>
            <p>{formatDateTime(data.experiment.started_at ?? "")}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.strategy")}</p>
            <p>
              {data.experiment.strategy_name} v{data.experiment.strategy_version}
            </p>
          </div>
          <div>
            <p className="text-muted">{t("competition.instrument")}</p>
            <p>{data.experiment.instrument}</p>
          </div>
          <div>
            <p className="text-muted">{t("common.status")}</p>
            <Badge>{t("competition.status_active")}</Badge>
          </div>
          <div>
            <p className="text-muted">{t("competition.total_initial")}</p>
            <p>{formatCurrency(data.experiment.total_initial_capital)}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.portfolios_split")}</p>
            <p>{t("competition.portfolios_split")}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.combined_equity")}</p>
            <p className="font-mono">{formatCurrency(data.combined?.current_equity ?? 0)}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.combined_pnl")}</p>
            <PnLDisplay value={data.combined?.combined_pnl ?? 0} size="sm" />
          </div>
        </CardContent>
      </Card>

      <div className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {data.portfolios?.map((p) => (
          <Card key={p.id} className="flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{p.name}</CardTitle>
              <p className="text-xs text-muted">
                {translateRiskProfile(p.risk_slug)} · {t("competition.risk_per_trade")}:{" "}
                {p.risk_per_trade_pct.toFixed(2)}%
              </p>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.current_equity")}</span>
                <span className="font-mono">{formatCurrency(p.equity)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.pnl")}</span>
                <PnLDisplay value={p.total_pnl} size="sm" />
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.return_pct")}</span>
                <span className={p.return_pct >= 0 ? "text-profit" : "text-loss"}>
                  {formatPercent(p.return_pct)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.open_position")}</span>
                <span>{p.open_position ? t("competition.open_position") : t("competition.no_position")}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.trades")}</span>
                <span>{p.trades_count}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.win_rate")}</span>
                <span>{p.win_rate != null ? `${p.win_rate.toFixed(1)}%` : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.max_drawdown")}</span>
                <span className="text-loss">{formatPercent(-p.max_drawdown_pct)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.exposure")}</span>
                <span>{p.exposure_pct.toFixed(1)}%</span>
              </div>
              {p.virtual_leverage != null && p.virtual_leverage > 1 ? (
                <div className="flex justify-between">
                  <span className="text-muted">{t("competition.virtual_leverage")}</span>
                  <span>{p.virtual_leverage.toFixed(2)}×</span>
                </div>
              ) : null}
              {p.actual_risk_pct != null ? (
                <div className="flex justify-between">
                  <span className="text-muted">{t("competition.actual_risk_label")}</span>
                  <span>{p.actual_risk_pct.toFixed(2)}%</span>
                </div>
              ) : null}
              <Link
                href={`/portfolio?portfolio_id=${p.id}`}
                className="mt-auto pt-2 text-sm text-accent hover:underline"
              >
                {t("competition.view_portfolio")} →
              </Link>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.leaderboard")}</CardTitle>
          <p className="text-xs text-muted">{t("competition.leader_return")}</p>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 text-right">{t("competition.rank")}</th>
                <th className="py-2 text-right">{t("common.name")}</th>
                <th className="py-2 text-right">{t("competition.return_pct")}</th>
                <th className="py-2 text-right">{t("competition.max_drawdown")}</th>
                <th className="py-2 text-right">{t("competition.realized_pnl")}</th>
                <th className="py-2 text-right">{t("competition.trades")}</th>
                <th className="py-2 text-right">{t("competition.return_vs_drawdown")}</th>
              </tr>
            </thead>
            <tbody>
              {data.leaderboard?.map((row) => (
                <tr key={row.portfolio_id} className="border-b border-border/50">
                  <td className="py-2 font-mono">{row.rank}</td>
                  <td className="py-2">{row.name}</td>
                  <td className="py-2 font-mono">{formatPercent(row.return_pct)}</td>
                  <td className="py-2 font-mono text-loss">
                    {formatPercent(-row.max_drawdown_pct)}
                  </td>
                  <td className="py-2 font-mono">{formatCurrency(row.realized_pnl)}</td>
                  <td className="py-2">{row.trades_count}</td>
                  <td className="py-2 font-mono">
                    {row.return_vs_drawdown != null ? row.return_vs_drawdown.toFixed(2) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.equity_comparison")}</CardTitle>
        </CardHeader>
        <CardContent>
          <MultiEquityCurveChart series={chartSeries} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("competition.activity")}</CardTitle>
        </CardHeader>
        <CardContent>
          {!data.activity?.length ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {data.activity.map((item, i) => (
                <li key={`${item.timestamp}-${i}`} className="flex gap-3 border-b border-border/40 pb-2">
                  <span className="shrink-0 font-mono text-xs text-muted">
                    {formatRelativeTime(item.timestamp)}
                  </span>
                  <span>{item.message}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
