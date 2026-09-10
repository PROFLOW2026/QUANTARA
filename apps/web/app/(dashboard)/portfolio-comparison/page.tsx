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
import {
  loadCompetitionFull,
  loadCompetitionView,
} from "@/lib/competition-client";
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
            <th className="py-2 text-right">{t("competition.closed_trades")}</th>
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

function portfolioDirectionLabel(direction?: string | null): string {
  if (!direction) return "—";
  const lower = direction.toLowerCase();
  if (lower === "long") return t("common.long");
  if (lower === "short") return t("common.short");
  return direction;
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
          <span className="text-muted">{t("competition.open_position_label")}</span>
          <span>
            {p.open_position
              ? t("competition.open_position_yes")
              : t("competition.open_position_no")}
          </span>
        </div>
        {p.open_position ? (
          <div className="flex justify-between">
            <span className="text-muted">{t("competition.open_direction")}</span>
            <span>{portfolioDirectionLabel(p.open_direction)}</span>
          </div>
        ) : null}
        <div className="flex justify-between">
          <span className="text-muted">{t("competition.unrealized_pnl")}</span>
          <PnLDisplay value={Number(p.unrealized_pnl ?? 0)} size="sm" />
        </div>
        <div className="flex justify-between">
          <span className="text-muted">{t("competition.closed_trades")}</span>
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
  const [enrichmentLoading, setEnrichmentLoading] = useState(false);
  const [enrichmentFailed, setEnrichmentFailed] = useState(false);

  const fetchData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);

    try {
      let core: CompetitionResponse;
      try {
        core = await loadCompetitionView();
      } catch (coreErr) {
        setData(null);
        if (isEngineConnectionError(coreErr)) {
          setConnectionError(true);
          setError(t("common.engine_connection_error"));
        } else if (coreErr instanceof ApiError) {
          setConnectionError(false);
          setError(`${t("competition.load_error_api")} (${coreErr.status})`);
        } else {
          setConnectionError(false);
          setError(t("competition.load_error_generic"));
        }
        setEnrichmentFailed(false);
        setEnrichmentLoading(false);
        return;
      }

      if (!core?.active || !core.experiment || !core.portfolios?.length) {
        setData(core);
        setConnectionError(false);
        setEnrichmentFailed(false);
        setEnrichmentLoading(false);
        setError(
          !core?.active
            ? t("competition.load_error_inactive")
            : t("competition.load_error_empty")
        );
        return;
      }

      setData(core);
      setError(null);
      setConnectionError(false);
      setLoading(false);
      setEnrichmentLoading(true);
      setEnrichmentFailed(false);

      try {
        const enriched = await loadCompetitionFull();
        setData((prev) =>
          prev
            ? {
                ...prev,
                ...enriched,
                experiment: { ...prev.experiment!, ...enriched.experiment! },
                combined: enriched.combined ?? prev.combined,
                portfolios: enriched.portfolios?.length
                  ? enriched.portfolios
                  : prev.portfolios,
                timeframe_groups:
                  enriched.timeframe_groups ?? prev.timeframe_groups,
                leaderboard: enriched.leaderboard ?? prev.leaderboard,
                leaderboards_by_timeframe:
                  enriched.leaderboards_by_timeframe ??
                  prev.leaderboards_by_timeframe,
                timeframe_comparison:
                  enriched.timeframe_comparison ?? prev.timeframe_comparison,
                equity_curves: enriched.equity_curves ?? prev.equity_curves,
                activity: enriched.activity ?? prev.activity,
                open_positions: enriched.open_positions ?? prev.open_positions,
                closed_trades: enriched.closed_trades ?? prev.closed_trades,
                today_summary: enriched.today_summary ?? prev.today_summary,
              }
            : enriched
        );
        setEnrichmentFailed(false);
      } catch {
        setEnrichmentFailed(true);
      } finally {
        setEnrichmentLoading(false);
      }
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
      setEnrichmentFailed(false);
      setEnrichmentLoading(false);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData(true);
    const id = window.setInterval(() => {
      void fetchData(false);
    }, POLL_INTERVAL);
    return () => window.clearInterval(id);
  }, [fetchData]);

  if (loading && !data) {
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

  const secondaryUnavailable =
    enrichmentFailed && !enrichmentLoading ? t("common.section_unavailable") : null;

  return (
    <>
      <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" />

      {enrichmentLoading ? (
        <p className="mb-4 text-xs text-muted">{t("common.loading")}</p>
      ) : null}

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
            <p>{formatCurrency(data.experiment.total_initial_capital ?? 0)}</p>
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
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : (
            <LeaderboardTable rows={data.leaderboard ?? []} />
          )}
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
              {secondaryUnavailable ? (
                <p className="text-sm text-muted">{secondaryUnavailable}</p>
              ) : (
                <LeaderboardTable rows={rows} />
              )}
            </CardContent>
          </Card>
        );
      })}

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.timeframe_comparison")}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : !data.timeframe_comparison?.length ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted">
                  <th className="py-2 text-right">{t("competition.timeframe")}</th>
                  <th className="py-2 text-right">{t("competition.avg_return")}</th>
                  <th className="py-2 text-right">{t("competition.closed_trades")}</th>
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
          <CardTitle>{t("competition.today_summary")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 text-sm">
          {secondaryUnavailable ? (
            <p className="text-muted">{secondaryUnavailable}</p>
          ) : (
            <>
              <div>
                <span className="text-muted">{t("competition.today_buy_signals")}</span>
                <p className="font-mono">{data.today_summary?.entry_signals_today ?? 0}</p>
              </div>
              <div>
                <span className="text-muted">{t("competition.today_sell_signals")}</span>
                <p className="font-mono">{data.today_summary?.sell_signals_today ?? 0}</p>
              </div>
              <div>
                <span className="text-muted">{t("competition.today_opened")}</span>
                <p className="font-mono">{data.today_summary?.trades_opened_today ?? 0}</p>
              </div>
              <div>
                <span className="text-muted">{t("competition.today_closed")}</span>
                <p className="font-mono">{data.today_summary?.trades_closed_today ?? 0}</p>
              </div>
              <div>
                <span className="text-muted">{t("competition.today_realized_pnl")}</span>
                <PnLDisplay value={data.today_summary?.realized_pnl_today ?? 0} size="sm" />
              </div>
              <div>
                <span className="text-muted">{t("competition.today_unrealized_pnl")}</span>
                <PnLDisplay value={data.today_summary?.unrealized_pnl_total ?? 0} size="sm" />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.open_positions_now")}</CardTitle>
        </CardHeader>
        <CardContent>
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : !data.open_positions?.length ? (
            <p className="text-sm text-muted">{t("competition.no_position")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted">
                    <th className="py-2 text-right">{t("common.name")}</th>
                    <th className="py-2 text-right">{t("competition.open_direction")}</th>
                    <th className="py-2 text-right">{t("competition.entry_price")}</th>
                    <th className="py-2 text-right">{t("market.current_price")}</th>
                    <th className="py-2 text-right">{t("competition.stop_loss")}</th>
                    <th className="py-2 text-right">{t("competition.take_profit")}</th>
                    <th className="py-2 text-right">{t("competition.unrealized_pnl")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.open_positions.map((pos) => (
                    <tr key={pos.portfolio_id} className="border-b border-border/50">
                      <td className="py-2">{pos.portfolio_name}</td>
                      <td className="py-2">{portfolioDirectionLabel(pos.direction)}</td>
                      <td className="py-2 font-mono">{formatCurrency(pos.entry_price)}</td>
                      <td className="py-2 font-mono">{formatCurrency(pos.current_price)}</td>
                      <td className="py-2 font-mono">{formatCurrency(pos.stop_loss)}</td>
                      <td className="py-2 font-mono">
                        {pos.take_profit != null ? formatCurrency(pos.take_profit) : "—"}
                      </td>
                      <td className="py-2">
                        <PnLDisplay value={pos.unrealized_pnl} size="sm" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.closed_trades_section")}</CardTitle>
        </CardHeader>
        <CardContent>
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : !data.closed_trades?.length ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted">
                    <th className="py-2 text-right">{t("common.name")}</th>
                    <th className="py-2 text-right">{t("competition.entry_price")}</th>
                    <th className="py-2 text-right">{t("competition.exit_price")}</th>
                    <th className="py-2 text-right">{t("competition.exit_reason")}</th>
                    <th className="py-2 text-right">{t("competition.realized_pnl")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.closed_trades.map((trade) => (
                    <tr key={trade.trade_id} className="border-b border-border/50">
                      <td className="py-2">{trade.portfolio_name}</td>
                      <td className="py-2 font-mono">{formatCurrency(trade.entry_price)}</td>
                      <td className="py-2 font-mono">{formatCurrency(trade.exit_price)}</td>
                      <td className="py-2">
                        {trade.exit_reason === "sl"
                          ? t("competition.exit_sl")
                          : trade.exit_reason === "tp"
                            ? t("competition.exit_tp")
                            : trade.exit_reason}
                      </td>
                      <td className="py-2">
                        <PnLDisplay value={trade.realized_pnl} size="sm" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.equity_comparison")}</CardTitle>
        </CardHeader>
        <CardContent>
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : (
            <MultiEquityCurveChart series={chartSeries} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("competition.activity")}</CardTitle>
        </CardHeader>
        <CardContent>
          {secondaryUnavailable ? (
            <p className="text-sm text-muted">{secondaryUnavailable}</p>
          ) : !data.activity?.length ? (
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
