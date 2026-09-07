"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  PageHeader,
  ErrorBanner,
  EngineConnectionError,
} from "@/components/layout/PageHeader";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  ApiError,
  isEngineConnectionError,
  type CompetitionLeaderboardRow,
  type CompetitionPortfolioSummary,
  type CompetitionResponse,
  type CompetitionTimeframeGroup,
} from "@/lib/api-client";
import { loadCompetitionFull, loadCompetitionView } from "@/lib/competition-client";
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
const TIMEFRAME_ORDER = ["1h", "15m", "5m"] as const;

function portfolioRiskLabel(p: CompetitionPortfolioSummary): string {
  const pct = Number(p.risk_per_trade_pct ?? p.target_risk_pct ?? 0);
  return `${pct.toFixed(2)}%`;
}

function LeaderboardTable({ rows }: { rows: CompetitionLeaderboardRow[] }) {
  if (!rows.length) {
    return <p className="text-sm text-muted">{t("common.no_data")}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted">
            <th className="py-2 text-right">{t("competition.rank")}</th>
            <th className="py-2 text-right">{t("common.name")}</th>
            <th className="py-2 text-right">{t("competition.timeframe")}</th>
            <th className="py-2 text-right">{t("competition.return_pct")}</th>
            <th className="py-2 text-right">{t("competition.max_drawdown")}</th>
            <th className="py-2 text-right">{t("competition.trades")}</th>
            <th className="py-2 text-right">{t("competition.win_rate")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.portfolio_id} className="border-b border-border/50">
              <td className="py-2 font-mono">{row.rank}</td>
              <td className="py-2">{row.name}</td>
              <td className="py-2">{row.timeframe_he ?? row.timeframe}</td>
              <td className="py-2 font-mono">{formatPercent(row.return_pct)}</td>
              <td className="py-2 font-mono text-loss">
                {formatPercent(-row.max_drawdown_pct)}
              </td>
              <td className="py-2">{row.trades_count}</td>
              <td className="py-2">
                {row.win_rate != null ? `${row.win_rate.toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PortfolioCard({ p }: { p: CompetitionPortfolioSummary }) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{p.name}</CardTitle>
        <p className="text-xs text-muted">
          {p.timeframe_he ?? p.timeframe} · {translateRiskProfile(p.risk_slug)} ·{" "}
          {t("competition.risk_per_trade")}: {portfolioRiskLabel(p)}
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
            {p.open_position ? t("competition.open_position") : t("competition.no_position")}
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
  );
}

function TimeframeSection({ group }: { group: CompetitionTimeframeGroup }) {
  return (
    <section className="mb-6">
      <h2 className="mb-3 text-lg font-semibold">{group.title_he}</h2>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {group.portfolios.map((p) => (
          <PortfolioCard key={p.id} p={p} />
        ))}
      </div>
    </section>
  );
}

export default function PortfolioComparisonPage() {
  const [data, setData] = useState<CompetitionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);

    try {
      let res: CompetitionResponse;
      try {
        res = await loadCompetitionFull();
      } catch (fullErr) {
        if (!isEngineConnectionError(fullErr)) {
          res = await loadCompetitionView();
        } else {
          throw fullErr;
        }
      }

      if (!res?.active || !res.experiment || !res.portfolios?.length) {
        setData(res);
        setConnectionError(false);
        setError(
          !res?.active
            ? t("competition.load_error_inactive")
            : t("competition.load_error_empty")
        );
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
      } else if (e instanceof ApiError) {
        setConnectionError(false);
        setError(`${t("competition.load_error_api")} (${e.status})`);
      } else {
        setConnectionError(false);
        setError(t("competition.load_error_generic"));
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

  const timeframeGroups =
    data.timeframe_groups ??
    TIMEFRAME_ORDER.map((timeframe) => ({
      timeframe,
      timeframe_he: timeframe,
      title_he: timeframe,
      portfolios: data.portfolios?.filter((p) => p.timeframe === timeframe) ?? [],
    })).filter((group) => group.portfolios.length > 0);

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
            <p>{formatCurrency(data.experiment.total_initial_capital ?? 30000)}</p>
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

      {timeframeGroups.map((group) => (
        <TimeframeSection key={group.timeframe} group={group} />
      ))}

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.leaderboard_overall")}</CardTitle>
          <p className="text-xs text-muted">{t("competition.leader_return")}</p>
        </CardHeader>
        <CardContent>
          <LeaderboardTable rows={data.leaderboard ?? []} />
        </CardContent>
      </Card>

      {TIMEFRAME_ORDER.map((timeframe) => {
        const rows = data.leaderboards_by_timeframe?.[timeframe] ?? [];
        if (!rows.length) return null;
        const title =
          timeframe === "1h"
            ? t("competition.leaderboard_1h")
            : timeframe === "15m"
              ? t("competition.leaderboard_15m")
              : t("competition.leaderboard_5m");
        return (
          <Card key={timeframe} className="mb-4">
            <CardHeader>
              <CardTitle>{title}</CardTitle>
            </CardHeader>
            <CardContent>
              <LeaderboardTable rows={rows} />
            </CardContent>
          </Card>
        );
      })}

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.timeframe_comparison")}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {!data.timeframe_comparison?.length ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2 text-right">{t("competition.timeframe")}</th>
                  <th className="py-2 text-right">{t("competition.avg_return")}</th>
                  <th className="py-2 text-right">{t("competition.trades")}</th>
                  <th className="py-2 text-right">{t("competition.avg_drawdown")}</th>
                  <th className="py-2 text-right">{t("competition.max_drawdown")}</th>
                </tr>
              </thead>
              <tbody>
                {data.timeframe_comparison.map((row) => (
                  <tr key={row.timeframe} className="border-b border-border/50">
                    <td className="py-2">{row.title_he}</td>
                    <td className="py-2 font-mono">
                      {formatPercent(row.average_return_pct)}
                    </td>
                    <td className="py-2">{row.total_trades}</td>
                    <td className="py-2 font-mono text-loss">
                      {formatPercent(-row.average_drawdown_pct)}
                    </td>
                    <td className="py-2 font-mono text-loss">
                      {formatPercent(-row.max_drawdown_pct)}
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
