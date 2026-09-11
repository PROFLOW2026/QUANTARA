"use client";

import { ModalLink } from "@/components/layout/ModalLink";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { useModuleData } from "@/hooks/useModuleData";
import type { ModuleProps } from "@/lib/modal-workspace/types";

import { EquityCurveChart } from "@/components/charts/EquityCurveChart";

import { DrawdownChart } from "@/components/charts/DrawdownChart";

import { MetricCard, MetricCardCurrency } from "@/components/trading/MetricCard";

import { DirectionBadge } from "@/components/trading/DirectionBadge";

import { PnLDisplay } from "@/components/trading/PnLDisplay";

import { StrategyParametersSummary } from "@/components/trading/StrategyParametersSummary";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import {

  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,

} from "@/components/ui/table";

import { api, ApiError } from "@/lib/api-client";

import { getBacktestDrawdownCurve, getBacktestEquityCurve } from "@/lib/chart-data";

import { formatMetricOrInsufficient, shortenHash, translateExitReason } from "@/lib/display-text";

import { t } from "@/lib/i18n";

import { formatCurrency, formatDateTime, formatPercent, formatPrice } from "@/lib/utils";



export default function BacktestDetailModule({ embedded, params = {} }: ModuleProps) {
  const id = params.id ?? "";
  const { data, error, loading } = useModuleData(async () => {
    const [backtest, trades] = await Promise.all([
      api.getBacktest(id),
      api.getBacktestTrades(id),
    ]);
    return { backtest, trades };
  }, [id]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const backtest = data?.backtest ?? null;
  const trades = data?.trades ?? null;
  const metrics = backtest?.metrics;

  const tradeCount = backtest?.trades_count ?? trades?.length ?? 0;

  const smallSample = tradeCount > 0 && tradeCount <= 2;

  const equityCurve = getBacktestEquityCurve(metrics, trades);

  const drawdownCurve = getBacktestDrawdownCurve(metrics, equityCurve);

  const orbAnalytics = metrics?.orb_analytics;



  return (

    <>

      {!embedded ? (
        <PageHeader
          titleKey="backtests.detail_title"
          action={
            <ModalLink href="/backtests" className="text-sm text-accent hover:underline">
              ← {t("common.back")}
            </ModalLink>
          }
        />
      ) : null}

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}



      {backtest && (

        <>

          <div className="mb-4 flex flex-wrap items-center gap-3">

            <StatusBadge status={backtest.status} />

            {backtest.status === "running" && backtest.progress && (

              <span className="text-sm text-muted">

                {t("backtests.progress")}: {backtest.progress.processed}/{backtest.progress.total}{" "}

                {t("backtests.candles_processed")}

              </span>

            )}

            {smallSample && (

              <p className="text-sm text-warning">{t("backtests.small_sample_note")}</p>

            )}

          </div>



          <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">

            <MetricCard

              label={t("backtests.return")}

              value={backtest.return_pct != null ? formatPercent(backtest.return_pct) : "—"}

            />

            <MetricCard

              label={t("backtests.win_rate")}

              value={formatMetricOrInsufficient(

                backtest.win_rate,

                (v) => `${v.toFixed(1)}%`,

                tradeCount

              )}

            />

            <MetricCard

              label={t("backtests.profit_factor")}

              value={formatMetricOrInsufficient(

                backtest.profit_factor,

                (v) => v.toFixed(2),

                tradeCount

              )}

            />

            <MetricCard

              label={t("backtests.max_dd")}

              value={backtest.max_drawdown_pct != null ? formatPercent(-backtest.max_drawdown_pct) : "—"}

            />

            <MetricCard

              label={t("backtests.trades")}

              value={tradeCount}

            />

          </div>



          <div className="mb-4 grid gap-4 lg:grid-cols-2">

            <Card>

              <CardHeader><CardTitle>{t("backtests.equity_curve")}</CardTitle></CardHeader>

              <CardContent>

                <EquityCurveChart data={equityCurve} />

              </CardContent>

            </Card>

            <Card>

              <CardHeader><CardTitle>{t("backtests.drawdown_chart")}</CardTitle></CardHeader>

              <CardContent>

                <DrawdownChart data={drawdownCurve} />

              </CardContent>

            </Card>

          </div>



          {metrics && (

            <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">

              <MetricCard

                label={t("backtests.winning_trades")}

                value={metrics.winning_trades ?? "—"}

              />

              <MetricCard

                label={t("backtests.losing_trades")}

                value={metrics.losing_trades ?? "—"}

              />

              <MetricCardCurrency

                label={t("backtests.avg_win")}

                value={metrics.average_win ?? 0}

              />

              <MetricCardCurrency

                label={t("backtests.avg_loss")}

                value={metrics.average_loss ?? 0}

              />

              <MetricCardCurrency

                label={t("backtests.best_trade")}

                value={metrics.best_trade ?? 0}

              />

              <MetricCardCurrency

                label={t("backtests.worst_trade")}

                value={metrics.worst_trade ?? 0}

              />

              <MetricCard

                label={t("backtests.expectancy")}

                value={formatMetricOrInsufficient(

                  metrics.expectancy,

                  (v) => v.toFixed(2),

                  tradeCount

                )}

              />

              <MetricCardCurrency

                label={t("backtests.final_capital")}

                value={metrics.final_capital ?? 0}

              />

            </div>

          )}



          <div className="mb-4 grid gap-4 lg:grid-cols-2">

            <Card>

              <CardHeader><CardTitle>{t("backtests.parameters_used")}</CardTitle></CardHeader>

              <CardContent>

                <StrategyParametersSummary parameters={backtest.parameters} />

              </CardContent>

            </Card>

            <Card>

              <CardHeader><CardTitle>{t("backtests.execution_assumptions")}</CardTitle></CardHeader>

              <CardContent className="space-y-2 text-sm">

                {backtest.execution_assumptions ? (

                  Object.entries(backtest.execution_assumptions).map(([key, value]) => (

                    <p key={key}>

                      <span className="text-muted">{key}: </span>

                      <span className="font-mono">{String(value)}</span>

                    </p>

                  ))

                ) : (

                  <p className="text-muted">{t("common.no_data")}</p>

                )}

              </CardContent>

            </Card>

          </div>



          {(backtest.parameters || backtest.execution_assumptions || backtest.dataset_fingerprint) && (

            <details className="mb-4 rounded-md border border-border bg-surface-elevated/30 p-3">

              <summary className="cursor-pointer text-sm text-accent">

                {t("common.show_advanced")}

              </summary>

              <div className="mt-3 space-y-3 text-xs">

                {backtest.parameters && (

                  <pre className="overflow-x-auto rounded bg-surface-elevated p-3 text-muted">

                    {JSON.stringify(backtest.parameters, null, 2)}

                  </pre>

                )}

                {backtest.execution_assumptions && (

                  <pre className="overflow-x-auto rounded bg-surface-elevated p-3 text-muted">

                    {JSON.stringify(backtest.execution_assumptions, null, 2)}

                  </pre>

                )}

                {backtest.dataset_fingerprint && (

                  <p>

                    <span className="text-muted">{t("backtests.dataset_fingerprint")}: </span>

                    <code className="font-mono">{shortenHash(backtest.dataset_fingerprint, 12)}</code>

                  </p>

                )}

              </div>

            </details>

          )}



          {orbAnalytics && (
            <div className="mb-4 grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader><CardTitle>{t("backtests.orb_weekday")}</CardTitle></CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("backtests.weekday")}</TableHead>
                        <TableHead>{t("backtests.trades")}</TableHead>
                        <TableHead>{t("backtests.win_rate")}</TableHead>
                        <TableHead>{t("positions.pnl")}</TableHead>
                        <TableHead>{t("backtests.average_r")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {orbAnalytics.weekday_breakdown.map((row) => (
                        <TableRow key={row.weekday}>
                          <TableCell>{row.weekday}</TableCell>
                          <TableCell>{row.trades}</TableCell>
                          <TableCell>{row.trades ? `${row.win_rate.toFixed(1)}%` : "—"}</TableCell>
                          <TableCell>{formatCurrency(row.net_pnl)}</TableCell>
                          <TableCell>{row.average_R != null ? row.average_R.toFixed(2) : "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle>{t("backtests.orb_range_width")}</CardTitle></CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("backtests.range_bucket")}</TableHead>
                        <TableHead>{t("backtests.trades")}</TableHead>
                        <TableHead>{t("backtests.win_rate")}</TableHead>
                        <TableHead>{t("positions.pnl")}</TableHead>
                        <TableHead>{t("backtests.expectancy")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {orbAnalytics.range_width_breakdown.map((row) => (
                        <TableRow key={row.range_bucket}>
                          <TableCell>{row.range_bucket}</TableCell>
                          <TableCell>{row.trades}</TableCell>
                          <TableCell>{row.trades ? `${row.win_rate.toFixed(1)}%` : "—"}</TableCell>
                          <TableCell>{formatCurrency(row.net_pnl)}</TableCell>
                          <TableCell>{row.trades ? row.expectancy.toFixed(2) : "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          )}

          <Card>

            <CardHeader><CardTitle>{t("backtests.trade_list")}</CardTitle></CardHeader>

            <CardContent>

              {!trades?.length ? (

                <EmptyState message={t("common.no_data")} />

              ) : (

                <Table>

                  <TableHeader>

                    <TableRow>

                      <TableHead>{t("common.date")}</TableHead>

                      <TableHead>{t("common.instrument")}</TableHead>

                      <TableHead>{t("common.direction")}</TableHead>

                      <TableHead>{t("journal.entry_exit")}</TableHead>

                      <TableHead>{t("positions.pnl")}</TableHead>

                      <TableHead>{t("journal.exit_reason")}</TableHead>

                    </TableRow>

                  </TableHeader>

                  <TableBody>

                    {trades.map((tr) => (

                      <TableRow key={tr.id}>

                        <TableCell>{formatDateTime(tr.close_time)}</TableCell>

                        <TableCell>{tr.instrument}</TableCell>

                        <TableCell><DirectionBadge direction={tr.direction} /></TableCell>

                        <TableCell className="font-mono text-xs">

                          {formatPrice(tr.entry_price)} → {formatPrice(tr.exit_price)}

                        </TableCell>

                        <TableCell><PnLDisplay value={tr.pnl} size="sm" /></TableCell>

                        <TableCell>{translateExitReason(tr.exit_reason)}</TableCell>

                      </TableRow>

                    ))}

                  </TableBody>

                </Table>

              )}

            </CardContent>

          </Card>

        </>

      )}

    </>

  );

}

