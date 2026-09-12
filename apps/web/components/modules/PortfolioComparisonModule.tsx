"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { PortfolioHierarchyAccordion } from "@/components/trading/PortfolioHierarchyAccordion";
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
  type CompetitionResponse,
} from "@/lib/api-client";
import {
  loadCompetitionFull,
  loadCompetitionView,
} from "@/lib/competition-client";
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
      <div className="flex h-[280px] items-center justify-center rounded-lg border border-dashed border-border border border-border-nested bg-surface-inner">
        <p className="text-sm text-muted">{t("common.loading")}</p>
      </div>
    ),
  }
);

const POLL_INTERVAL = 60_000;
const TIMEFRAME_ORDER = ["1h", "15m", "5m"] as const;
const RANKING_TIMEFRAME_ORDER = ["5m", "15m", "1h"] as const;

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

function CollapsibleRankingSection({
  title,
  rows,
  unavailable,
}: {
  title: string;
  rows: CompetitionLeaderboardRow[];
  unavailable: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const leader = rows[0];

  if (!rows.length && !unavailable) {
    return null;
  }

  return (
    <div className="rounded-md border border-border-nested bg-surface-inner">
      <button
        type="button"
        className="flex w-full items-start justify-between gap-3 px-3 py-3 text-right hover:bg-surface-inner-hover-soft"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
      >
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{title}</span>
          {leader ? (
            <span className="mt-1 block text-xs text-muted">
              {t("competition.ranking_collapsed_summary", {
                count: rows.length,
                leader: leader.name,
                return: formatPercent(leader.return_pct),
              })}
            </span>
          ) : null}
        </span>
        <span className="shrink-0 text-xs text-accent">
          {expanded ? t("competition.collapse_group") : t("competition.expand_group")}
        </span>
      </button>
      {expanded ? (
        <div className="max-h-[min(70vh,520px)] overflow-auto border-t border-border/60 p-3">
          {unavailable ? (
            <p className="text-sm text-muted">{unavailable}</p>
          ) : (
            <LeaderboardTable rows={rows} />
          )}
        </div>
      ) : null}
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

export default function PortfolioComparisonModule({ embedded }: ModuleProps) {
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

    let intervalId: number | undefined;

    const startPolling = () => {
      if (intervalId !== undefined) return;
      intervalId = window.setInterval(() => {
        if (document.visibilityState === "visible") {
          void fetchData(false);
        }
      }, POLL_INTERVAL);
    };

    const stopPolling = () => {
      if (intervalId === undefined) return;
      window.clearInterval(intervalId);
      intervalId = undefined;
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void fetchData(false);
        startPolling();
      } else {
        stopPolling();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") {
      startPolling();
    }

    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [fetchData]);

  if (loading && !data) {
    return (
      <>
        {!embedded ? <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" /> : null}
        <p className="text-muted">{t("common.loading")}</p>
      </>
    );
  }

  if (error || !data?.active || !data.experiment || !data.portfolios?.length) {
    return (
      <>
        {!embedded ? <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" /> : null}
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

  const secondaryUnavailable =
    enrichmentFailed && !enrichmentLoading ? t("common.section_unavailable") : null;

  return (
    <>
      {!embedded ? <PageHeader titleKey="competition.title" subtitleKey="competition.subtitle" /> : null}

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

      <PortfolioHierarchyAccordion portfolios={data.portfolios} />

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>{t("competition.rankings_section_title")}</CardTitle>
          <p className="text-xs text-muted">{t("competition.rankings_section_hint")}</p>
        </CardHeader>
        <CardContent className="space-y-2">
          <CollapsibleRankingSection
            title={t("competition.leaderboard_overall")}
            rows={data.leaderboard ?? []}
            unavailable={secondaryUnavailable}
          />
          {RANKING_TIMEFRAME_ORDER.map((timeframe) => {
            const rows = data.leaderboards_by_timeframe?.[timeframe] ?? [];
            const title =
              timeframe === "1h"
                ? t("competition.leaderboard_1h")
                : timeframe === "15m"
                  ? t("competition.leaderboard_15m")
                  : t("competition.leaderboard_5m");
            return (
              <CollapsibleRankingSection
                key={timeframe}
                title={title}
                rows={rows}
                unavailable={secondaryUnavailable}
              />
            );
          })}
        </CardContent>
      </Card>

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
