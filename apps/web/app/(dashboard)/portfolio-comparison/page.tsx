"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader, ErrorBanner, EngineConnectionError } from "@/components/layout/PageHeader";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { loadCompetitionView } from "@/lib/competition-client";
import {
  ApiError,
  isEngineConnectionError,
  type CompetitionPortfolioSummary,
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

const MultiEquityCurveChart = dynamic(
  () =>
    import("@/components/charts/MultiEquityCurveChart").then(
      (mod) => mod.MultiEquityCurveChart
    ),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[280px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-elevated/30">
        <p className="text-sm text-muted">{t("common.loading")}</p>
      </div>
    ),
  }
);

const POLL_INTERVAL = 60_000;

function normalizeCompetitionResponse(
  raw: CompetitionResponse | null | undefined
): CompetitionResponse | null {
  if (!raw || typeof raw !== "object") return null;
  return {
    ...raw,
    active: Boolean(raw.active),
    portfolios: Array.isArray(raw.portfolios) ? raw.portfolios : [],
    leaderboard: Array.isArray(raw.leaderboard) ? raw.leaderboard : [],
    activity: Array.isArray(raw.activity) ? raw.activity : [],
    equity_curves:
      raw.equity_curves && typeof raw.equity_curves === "object"
        ? raw.equity_curves
        : {},
    combined: raw.combined ?? {
      initial_equity: 0,
      current_equity: 0,
      combined_pnl: 0,
      open_positions_total: 0,
    },
    experiment: raw.experiment,
  };
}

function portfolioRiskLabel(p: CompetitionPortfolioSummary): string {
  const pct = Number(p.risk_per_trade_pct ?? p.target_risk_pct ?? 0);
  return `${pct.toFixed(2)}%`;
}

export default function PortfolioComparisonPage() {
  const [data, setData] = useState<CompetitionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);

    try {
      const res = normalizeCompetitionResponse(await loadCompetitionView());
      if (!res?.active || !res.experiment) {
        setData(res);
        setConnectionError(false);
        setError(t("competition.load_error_inactive"));
        return;
      }
      if (!res.portfolios?.length) {
        setData(res);
        setConnectionError(false);
        setError(t("competition.load_error_empty"));
        return;
      }
      setData(res);
      setError(null);
      setConnectionError(false);
    } catch (e) {
      setData(null);
      if (isEngineConnectionError(e)) {
        setConnectionError(true);
        setError(t("common.engine_connection_error"));
      } else {
        setConnectionError(false);
        if (e instanceof ApiError) {
          setError(`${t("competition.load_error_api")} (${e.status})`);
        } else {
          setError(t("competition.load_error_generic"));
        }
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData(true);
    const id = window.setInterval(() => {
      void fetchData(false);
    }, POLL_INTERVAL);
    return () => window.clearInterval(id);
  }, [fetchData]);

  if (loading) {
    return (
      <>
        <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />
        <p className="text-muted">{t("common.loading")}</p>
      </>
    );
  }

  if (error || !data?.active || !data.experiment || !data.portfolios?.length) {
    return (
      <>
        <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />
        {connectionError ? (
          <EngineConnectionError onRetry={() => void fetchData(true)} />
        ) : (
          <ErrorBanner message={error ?? t("common.no_data")} />
        )}
      </>
    );
  }

  const chartSeries = data.portfolios.map((p) => ({
    id: p.id,
    name: p.name,
    data: data.equity_curves?.[p.id] ?? [],
  }));

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
            <p>{formatCurrency(data.experiment.total_initial_capital ?? 10000)}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.portfolios_split")}</p>
            <p>{t("competition.portfolios_split")}</p>
          </div>
          <div>
            <p className="text-muted">{t("competition.combined_equity")}</p>
            <p className="font-mono">
              {formatCurrency(data.combined?.current_equity ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-muted">{t("competition.combined_pnl")}</p>
            <PnLDisplay value={data.combined?.combined_pnl ?? 0} size="sm" />
          </div>
        </CardContent>
      </Card>

      <div className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {data.portfolios.map((p) => (
          <Card key={p.id} className="flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{p.name}</CardTitle>
              <p className="text-xs text-muted">
                {translateRiskProfile(p.risk_slug)} · {t("competition.risk_per_trade")}:{" "}
                {portfolioRiskLabel(p)}
              </p>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.current_equity")}</span>
                <span className="font-mono">{formatCurrency(Number(p.equity ?? 0))}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("portfolio.initial_capital")}</span>
                <span className="font-mono">
                  {formatCurrency(Number(p.initial_capital ?? 2000))}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.pnl")}</span>
                <PnLDisplay value={Number(p.total_pnl ?? 0)} size="sm" />
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.return_pct")}</span>
                <span className={(p.return_pct ?? 0) >= 0 ? "text-profit" : "text-loss"}>
                  {formatPercent(Number(p.return_pct ?? 0))}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.open_position")}</span>
                <span>
                  {p.open_position
                    ? t("competition.open_position")
                    : t("competition.no_position")}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.trades")}</span>
                <span>{p.trades_count ?? 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.win_rate")}</span>
                <span>
                  {p.win_rate != null ? `${Number(p.win_rate).toFixed(1)}%` : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.max_drawdown")}</span>
                <span className="text-loss">
                  {formatPercent(-Number(p.max_drawdown_pct ?? 0))}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">{t("competition.exposure")}</span>
                <span>{Number(p.exposure_pct ?? 0).toFixed(1)}%</span>
              </div>
              {p.virtual_leverage != null && p.virtual_leverage > 1 ? (
                <div className="flex justify-between">
                  <span className="text-muted">{t("competition.virtual_leverage")}</span>
                  <span>{Number(p.virtual_leverage).toFixed(2)}×</span>
                </div>
              ) : null}
              {p.actual_risk_pct != null ? (
                <div className="flex justify-between">
                  <span className="text-muted">{t("competition.actual_risk_label")}</span>
                  <span>{Number(p.actual_risk_pct).toFixed(2)}%</span>
                </div>
              ) : null}
              <Link
                href={`/portfolio?portfolio_id=${encodeURIComponent(p.id)}`}
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
          {(data.leaderboard?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
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
                    <td className="py-2 font-mono">
                      {formatPercent(Number(row.return_pct ?? 0))}
                    </td>
                    <td className="py-2 font-mono text-loss">
                      {formatPercent(-Number(row.max_drawdown_pct ?? 0))}
                    </td>
                    <td className="py-2 font-mono">
                      {formatCurrency(Number(row.realized_pnl ?? 0))}
                    </td>
                    <td className="py-2">{row.trades_count ?? 0}</td>
                    <td className="py-2 font-mono">
                      {row.return_vs_drawdown != null
                        ? Number(row.return_vs_drawdown).toFixed(2)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
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
                <li
                  key={`${item.timestamp}-${i}`}
                  className="flex gap-3 border-b border-border/40 pb-2"
                >
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
