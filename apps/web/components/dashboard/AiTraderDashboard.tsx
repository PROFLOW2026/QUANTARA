"use client";

/**
 * AI Trader Paper Dashboard
 * Shows live state from paper_runner.py via /api/v1/ai-trader/summary.
 * Read-only. No trading controls. No broker connection.
 * Replay and backtest results are NEVER shown here.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { HomeSummaryCard, HomeSummaryValue } from "@/components/dashboard/HomeSummaryCard";
import type { AiTraderSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { cn } from "@/lib/utils";

type Props = {
  data: AiTraderSummary | null;
  loading: boolean;
};

/**
 * Shows runner process status separately from data staleness.
 * runner_status_text comes from the API and already contains the human-readable
 * Hebrew string (e.g. "פועל — הבורסה סגורה, ממתין למסחר").
 */
function RunnerStatusBadge({
  alive,
  statusText,
}: {
  alive: boolean;
  statusText: string;
}) {
  if (!alive) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-muted/20 px-2.5 py-0.5 text-xs font-medium text-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-muted" aria-hidden />
        {statusText || t("ai_trader.runner_stopped")}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-profit/10 px-2.5 py-0.5 text-xs font-medium text-profit">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-profit" aria-hidden />
      {statusText}
    </span>
  );
}

function DataStaleBadge({ stale, ageMin }: { stale: boolean; ageMin?: number | null }) {
  if (!stale) return null;
  const label = ageMin != null
    ? `${t("ai_trader.data_not_current")} (${Math.round(ageMin)}m)`
    : t("ai_trader.data_not_current");
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">
      <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden />
      {label}
    </span>
  );
}

function pf(v: number | null | undefined): string {
  if (v == null) return t("ai_trader.no_data");
  if (v >= 999) return "∞";
  return v.toFixed(2);
}

export function AiTraderDashboard({ data, loading }: Props) {
  if (loading && !data) {
    return <p className="text-muted">{t("common.loading")}</p>;
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted">
          {t("ai_trader.unavailable")}
        </CardContent>
      </Card>
    );
  }

  const openCount   = data.open_fills.length;
  const closedCount = data.closed_count;

  return (
    <>
      {/* ── Status bar ── */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <RunnerStatusBadge
            alive={data.runner_alive}
            statusText={data.runner_status_text ?? (data.runner_alive ? t("ai_trader.runner_running") : t("ai_trader.runner_stopped"))}
          />
          <DataStaleBadge stale={data.data_stale} ageMin={data.staleness_minutes} />
          {data.last_updated ? (
            <span className="text-xs text-muted">
              {t("ai_trader.last_updated")}: {data.last_updated}
            </span>
          ) : null}
        </div>
        <span className="text-xs text-muted">
          {t("ai_trader.strategy")}: {data.strategy}
        </span>
      </div>

      {/* ── Account summary cards ── */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <HomeSummaryCard label={t("ai_trader.equity")}>
          <HomeSummaryValue>{formatCurrency(data.capital)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("ai_trader.total_return")}>
          <PnLDisplay value={data.realized_pnl} size="lg" />
          <p className="mt-1 text-xs text-muted">
            {formatPercent(data.return_pct)}
          </p>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("ai_trader.cash")}>
          <HomeSummaryValue>{formatCurrency(data.cash)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("ai_trader.max_drawdown")}>
          <HomeSummaryValue>
            {formatCurrency(data.max_drawdown_usd)}
            <span className="ms-1 text-sm text-muted">
              ({formatPercent(data.max_drawdown_pct)})
            </span>
          </HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("ai_trader.open_positions")}>
          <HomeSummaryValue>{openCount}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("ai_trader.closed_trades")}>
          <HomeSummaryValue>{closedCount}</HomeSummaryValue>
        </HomeSummaryCard>
      </div>

      {/* ── Performance stats ── */}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("ai_trader.win_rate")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">
              {data.win_rate_pct != null ? `${data.win_rate_pct}%` : t("ai_trader.no_data")}
            </p>
            {closedCount > 0 && (
              <p className="mt-1 text-xs text-muted">
                {data.win_count}W / {data.loss_count}L / {data.flat_count}F
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("ai_trader.profit_factor")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{pf(data.profit_factor)}</p>
            {closedCount > 0 && (
              <p className="mt-1 text-xs text-muted">
                +{formatCurrency(data.gross_wins)} / -{formatCurrency(data.gross_losses)}
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("ai_trader.trades_today")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{data.trades_today}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("ai_trader.daily_loss_at_risk")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{formatCurrency(data.daily_loss_at_risk)}</p>
            <p className="mt-1 text-xs text-muted">
              {t("ai_trader.limit")}: {formatCurrency(data.max_daily_loss_usd)}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* ── Open positions ── */}
      <Card className="mt-4">
        <CardHeader><CardTitle>{t("ai_trader.open_positions_title")}</CardTitle></CardHeader>
        <CardContent>
          {openCount === 0 ? (
            <p className="text-sm text-muted">{t("ai_trader.no_open_positions")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted">
                    <th className="py-2 text-start">{t("ai_trader.symbol")}</th>
                    <th className="py-2 text-end">{t("ai_trader.shares")}</th>
                    <th className="py-2 text-end">{t("ai_trader.entry")}</th>
                    <th className="py-2 text-end">{t("ai_trader.stop")}</th>
                    <th className="py-2 text-end">{t("ai_trader.target")}</th>
                    <th className="py-2 text-end">{t("ai_trader.filled_at")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.open_fills.map((f, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-2 font-medium">{f.symbol}</td>
                      <td className="py-2 text-end font-mono">{f.shares}</td>
                      <td className="py-2 text-end font-mono">${f.entry_price_fill.toFixed(2)}</td>
                      <td className="py-2 text-end font-mono text-warning">${f.stop.toFixed(2)}</td>
                      <td className="py-2 text-end font-mono text-profit">${f.target.toFixed(2)}</td>
                      <td className="py-2 text-end text-muted">{f.filled_at ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Closed trades ── */}
      <Card className="mt-4">
        <CardHeader><CardTitle>{t("ai_trader.closed_trades_title")}</CardTitle></CardHeader>
        <CardContent>
          {closedCount === 0 ? (
            <p className="text-sm text-muted">{t("ai_trader.no_closed_trades")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted">
                    <th className="py-2 text-start">{t("ai_trader.symbol")}</th>
                    <th className="py-2 text-end">{t("ai_trader.shares")}</th>
                    <th className="py-2 text-end">{t("ai_trader.entry")}</th>
                    <th className="py-2 text-end">{t("ai_trader.exit_price")}</th>
                    <th className="py-2 text-start">{t("ai_trader.exit_reason")}</th>
                    <th className="py-2 text-end">R</th>
                    <th className="py-2 text-end">{t("ai_trader.pnl")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.closed_fills.map((f, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-2 font-medium">{f.symbol}</td>
                      <td className="py-2 text-end font-mono">{f.shares}</td>
                      <td className="py-2 text-end font-mono">${f.entry_price_fill.toFixed(2)}</td>
                      <td className="py-2 text-end font-mono">
                        {f.exit_price != null ? `$${f.exit_price.toFixed(2)}` : "—"}
                      </td>
                      <td className="py-2 text-muted">{f.exit_reason ?? "—"}</td>
                      <td className={cn(
                        "py-2 text-end font-mono",
                        (f.r ?? 0) > 0 ? "text-profit" : (f.r ?? 0) < 0 ? "text-loss" : "text-muted"
                      )}>
                        {f.r != null ? (f.r > 0 ? "+" : "") + f.r.toFixed(3) : "—"}
                      </td>
                      <td className="py-2 text-end">
                        {f.pnl != null ? <PnLDisplay value={f.pnl} size="sm" /> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Strategy & Risk config ── */}
      <Card className="mt-4">
        <CardHeader><CardTitle>{t("ai_trader.strategy_config")}</CardTitle></CardHeader>
        <CardContent className="grid gap-2 text-sm sm:grid-cols-2">
          <p><span className="text-muted">{t("ai_trader.strategy")}: </span>{data.strategy}</p>
          <p><span className="text-muted">{t("ai_trader.stocks")}: </span>{data.target_stocks.join(", ")}</p>
          <p><span className="text-muted">{t("ai_trader.max_risk_per_trade")}: </span>${data.max_risk_per_trade_usd}</p>
          <p><span className="text-muted">{t("ai_trader.max_daily_loss")}: </span>${data.max_daily_loss_usd}</p>
          <p><span className="text-muted">{t("ai_trader.starting_capital")}: </span>{formatCurrency(data.starting_capital)}</p>
        </CardContent>
      </Card>

      {/* ── Today's signals ── */}
      {data.signals_seen.length > 0 && (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("ai_trader.signals_today")}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm">
              {data.signals_seen.join(", ")}
            </p>
          </CardContent>
        </Card>
      )}

      {/* ── Data notice ── */}
      <p className="mt-4 text-xs text-muted">
        {t("ai_trader.data_notice")}
      </p>
    </>
  );
}
