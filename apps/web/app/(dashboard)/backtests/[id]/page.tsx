import Link from "next/link";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { EquityCurveChart } from "@/components/charts/EquityCurveChart";
import { DrawdownChart } from "@/components/charts/DrawdownChart";
import { MetricCard, MetricCardCurrency } from "@/components/trading/MetricCard";
import { DirectionBadge } from "@/components/trading/DirectionBadge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { getBacktestDrawdownCurve, getBacktestEquityCurve } from "@/lib/chart-data";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPercent, formatPrice } from "@/lib/utils";

interface Props {
  params: { id: string };
}

export default async function BacktestDetailPage({ params }: Props) {
  let backtest = null;
  let trades = null;
  let error: string | null = null;

  try {
    [backtest, trades] = await Promise.all([
      api.getBacktest(params.id),
      api.getBacktestTrades(params.id),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  const metrics = backtest?.metrics;
  const equityCurve = getBacktestEquityCurve(metrics, trades);
  const drawdownCurve = getBacktestDrawdownCurve(metrics, equityCurve);

  return (
    <>
      <PageHeader
        titleKey="backtests.detail_title"
        action={
          <Link href="/backtests" className="text-sm text-accent hover:underline">
            ← {t("common.back")}
          </Link>
        }
      />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {backtest && (
        <>
          <div className="mb-4 flex items-center gap-3">
            <StatusBadge status={backtest.status} />
            {backtest.status === "running" && backtest.progress && (
              <span className="text-sm text-muted">
                {t("backtests.progress")}: {backtest.progress.processed}/{backtest.progress.total}{" "}
                {t("backtests.candles_processed")}
              </span>
            )}
          </div>

          <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <MetricCard
              label={t("backtests.return")}
              value={backtest.return_pct != null ? formatPercent(backtest.return_pct) : "—"}
            />
            <MetricCard
              label={t("backtests.win_rate")}
              value={backtest.win_rate != null ? `${backtest.win_rate.toFixed(1)}%` : "—"}
            />
            <MetricCard
              label={t("backtests.profit_factor")}
              value={backtest.profit_factor?.toFixed(2) ?? "—"}
            />
            <MetricCard
              label={t("backtests.max_dd")}
              value={backtest.max_drawdown_pct != null ? formatPercent(-backtest.max_drawdown_pct) : "—"}
            />
            <MetricCard
              label={t("backtests.trades")}
              value={backtest.trades_count ?? 0}
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
                value={metrics.expectancy?.toFixed(2) ?? "—"}
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
                <pre className="overflow-x-auto rounded bg-surface-elevated p-3 text-xs">
                  {backtest.parameters ? JSON.stringify(backtest.parameters, null, 2) : "—"}
                </pre>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("backtests.execution_assumptions")}</CardTitle></CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded bg-surface-elevated p-3 text-xs">
                  {backtest.execution_assumptions
                    ? JSON.stringify(backtest.execution_assumptions, null, 2)
                    : "—"}
                </pre>
              </CardContent>
            </Card>
          </div>

          {backtest.dataset_fingerprint && (
            <Card className="mb-4">
              <CardHeader><CardTitle>{t("backtests.dataset_fingerprint")}</CardTitle></CardHeader>
              <CardContent>
                <code className="break-all font-mono text-xs">{backtest.dataset_fingerprint}</code>
              </CardContent>
            </Card>
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
                        <TableCell>{tr.exit_reason ?? "—"}</TableCell>
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
