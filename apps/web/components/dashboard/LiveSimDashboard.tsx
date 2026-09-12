"use client";

import { MetricCardCurrency } from "@/components/trading/MetricCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { LiveSimAccountSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatPercent } from "@/lib/utils";

type Props = {
  data: LiveSimAccountSummary | null;
  loading: boolean;
};

export function LiveSimDashboard({ data, loading }: Props) {
  if (loading && !data) {
    return <p className="text-muted">{t("common.loading")}</p>;
  }
  if (!data?.available) {
    return <p className="text-muted">{t("common.section_unavailable")}</p>;
  }

  return (
    <>
      <div className="mb-2">
        <h2 className="text-lg font-semibold">{t("home.live_sim_title")}</h2>
        <p className="text-sm text-muted">{t("home.live_sim_subtitle")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCardCurrency label={t("home.live_sim_starting_capital")} value={data.starting_capital ?? 10000} />
        <MetricCardCurrency label={t("home.live_sim_equity")} value={data.equity ?? 0} />
        <MetricCardCurrency label={t("home.live_sim_cash")} value={data.cash ?? 0} />
        <MetricCardCurrency label={t("home.live_sim_available_margin")} value={data.available_margin ?? 0} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.daily_pnl")}</CardTitle></CardHeader>
          <CardContent><PnLDisplay value={data.daily_pnl ?? 0} size="lg" /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.total_return")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">{formatPercent(data.total_return_pct ?? 0)}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.realized_pnl")}</CardTitle></CardHeader>
          <CardContent><PnLDisplay value={data.realized_pnl ?? 0} size="lg" /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.unrealized_pnl")}</CardTitle></CardHeader>
          <CardContent><PnLDisplay value={data.unrealized_pnl ?? 0} size="lg" /></CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.live_sim_drawdown")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{formatPercent(data.current_drawdown_pct ?? 0)}</p>
            <p className="mt-1 text-xs text-muted">
              {t("home.high_water_mark")}: ${(data.high_water_mark ?? 0).toLocaleString()}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.live_sim_sl_risk")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{formatPercent(data.open_sl_risk_pct ?? 0)}</p>
            <p className="mt-1 text-xs text-muted">${(data.open_sl_risk_usd ?? 0).toFixed(2)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.gross_exposure")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">${(data.gross_exposure ?? 0).toLocaleString()}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.live_sim_runtime")}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm">{data.runtime_duration_he ?? "—"}</p>
            <p className="mt-1 text-xs text-muted">{data.started_at ?? ""}</p>
          </CardContent>
        </Card>
      </div>

      {data.risk_settings ? (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("home.live_sim_risk_settings")}</CardTitle></CardHeader>
          <CardContent className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <p>{t("home.risk_per_trade")}: {formatPercent(data.risk_settings.risk_per_trade_pct)} ≈ ${data.risk_settings.risk_per_trade_usd_approx.toFixed(0)}</p>
            <p>{t("home.max_total_sl_risk")}: {formatPercent(data.risk_settings.max_total_open_sl_risk_pct)}</p>
            <p>{t("home.max_symbol_sl_risk")}: {formatPercent(data.risk_settings.max_symbol_sl_risk_pct)}</p>
            <p>{t("home.max_group_sl_risk")}: {formatPercent(data.risk_settings.max_group_sl_risk_pct)}</p>
            <p>{t("home.daily_loss_gate")}: {formatPercent(data.risk_settings.daily_loss_gate_pct)}</p>
            <p>{t("home.drawdown_gate")}: {formatPercent(data.risk_settings.max_drawdown_gate_pct)}</p>
          </CardContent>
        </Card>
      ) : null}

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.live_sim_open_positions")}</CardTitle></CardHeader>
        <CardContent>
          {(data.open_positions?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted">{t("home.no_open_positions")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted">
                    <th className="py-2 text-start">{t("positions.asset")}</th>
                    <th className="py-2 text-start">{t("positions.direction")}</th>
                    <th className="py-2 text-start">{t("home.robot")}</th>
                    <th className="py-2 text-end">{t("positions.quantity")}</th>
                    <th className="py-2 text-end">{t("positions.entry")}</th>
                    <th className="py-2 text-end">{t("home.sl_risk")}</th>
                    <th className="py-2 text-end">{t("home.unrealized_pnl")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.open_positions?.map((p) => (
                    <tr key={p.id} className="border-b border-border/50">
                      <td className="py-2">{p.symbol}</td>
                      <td className="py-2">{p.direction}</td>
                      <td className="py-2">{p.robot ?? p.strategy_slug}</td>
                      <td className="py-2 text-end font-mono">{p.quantity}</td>
                      <td className="py-2 text-end font-mono">{p.entry_price}</td>
                      <td className="py-2 text-end font-mono">${p.planned_sl_risk_usd.toFixed(2)}</td>
                      <td className="py-2 text-end"><PnLDisplay value={p.unrealized_pnl} size="sm" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.live_sim_recent_decisions")}</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {(data.recent_decisions?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted">{t("home.no_decisions_yet")}</p>
          ) : (
            data.recent_decisions?.map((row) => (
              <div key={row.id} className="rounded-md border border-border/60 p-3 text-sm">
                <p className="font-medium">
                  {row.robot_label ?? row.strategy_slug} — {row.symbol} ({row.timeframe})
                </p>
                <p className={row.accepted ? "text-success" : "text-warning"}>
                  {row.accepted ? t("home.decision_accepted") : t("home.decision_rejected")}
                  {row.calculated_risk_usd != null ? ` · סיכון: $${row.calculated_risk_usd.toFixed(2)}` : ""}
                </p>
                {!row.accepted && row.rejection_reason_he ? (
                  <p className="text-muted">{t("home.rejection_reason")}: {row.rejection_reason_he}</p>
                ) : null}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {data.candidates ? (
        <div className="mt-4 grid gap-4 sm:grid-cols-4">
          <Card><CardHeader><CardTitle>{t("home.candidates_seen")}</CardTitle></CardHeader><CardContent><p className="font-mono text-2xl">{data.candidates.total}</p></CardContent></Card>
          <Card><CardHeader><CardTitle>{t("home.candidates_accepted")}</CardTitle></CardHeader><CardContent><p className="font-mono text-2xl">{data.candidates.accepted}</p></CardContent></Card>
          <Card><CardHeader><CardTitle>{t("home.candidates_rejected")}</CardTitle></CardHeader><CardContent><p className="font-mono text-2xl">{data.candidates.rejected}</p></CardContent></Card>
          <Card><CardHeader><CardTitle>{t("home.acceptance_rate")}</CardTitle></CardHeader><CardContent><p className="font-mono text-2xl">{formatPercent(data.candidates.acceptance_rate_pct)}</p></CardContent></Card>
        </div>
      ) : null}
    </>
  );
}
