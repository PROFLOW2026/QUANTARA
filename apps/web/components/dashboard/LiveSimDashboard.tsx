"use client";

import { useState } from "react";
import { LiveSimAllocationSettingsPanel } from "@/components/dashboard/LiveSimAllocationSettings";
import { HomeSummaryCard, HomeSummaryValue } from "@/components/dashboard/HomeSummaryCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { LiveSimAccountSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";

type Props = {
  data: LiveSimAccountSummary | null;
  loading: boolean;
};

function liveSimAllocationStatusLabel(row: NonNullable<LiveSimAccountSummary["recent_decisions"]>[number]): string {
  if (row.live_sim_position_id || row.lifecycle_state === "filled") {
    return t("home.live_sim_state_filled");
  }
  if (row.broker_order_id) {
    return t("home.live_sim_state_order_created");
  }
  if (row.lifecycle_state === "expired" || row.metadata?.expired === true) {
    return t("home.live_sim_state_expired");
  }
  if (!row.accepted) {
    return t("home.live_sim_state_rejected");
  }
  if (row.lifecycle_state === "pending_execution" || row.metadata?.pending_execution === true) {
    return t("home.live_sim_state_pending");
  }
  return t("home.live_sim_state_accepted");
}

export function LiveSimDashboard({ data, loading }: Props) {
  const [decisionsExpanded, setDecisionsExpanded] = useState(false);

  if (loading && !data) {
    return <p className="text-muted">{t("common.loading")}</p>;
  }
  if (!data?.available) {
    return <p className="text-muted">{t("common.section_unavailable")}</p>;
  }

  const candidates = data.candidates ?? {
    total: 0,
    accepted: 0,
    rejected: 0,
    acceptance_rate_pct: 0,
    orders_sent: 0,
    fills: 0,
    positions_opened: 0,
  };
  const decisionCount = candidates.total;
  const decisionsToggleLabel = decisionsExpanded
    ? t("home.live_sim_hide_recent_decisions")
    : t("home.live_sim_show_recent_decisions", { count: decisionCount });

  const openCount = data.open_positions?.length ?? 0;
  const closedPositions =
    data.closed_positions_count ?? data.closed_trades?.length ?? data.closed_trades_count ?? 0;
  const exitFills = data.exit_fills_count ?? data.closed_trades_count;
  const riskVis = data.risk_visibility;
  const cleanWin = data.clean_window;
  const v32 = data.v32_experiment;
  const v32OpenUnrealized = (data.open_positions ?? []).reduce(
    (sum, p) => sum + (p.unrealized_pnl ?? 0),
    0
  );
  const v32Realized = cleanWin?.net_pnl ?? 0;
  const v32Unrealized = v32 ? v32OpenUnrealized : (data.unrealized_pnl ?? 0);
  const v32TotalPnl = v32 ? v32Realized + v32Unrealized : undefined;
  const displayRealized = v32 ? v32Realized : (data.realized_pnl ?? 0);
  const displayUnrealized = v32 ? v32Unrealized : (data.unrealized_pnl ?? 0);
  const displayTotalPnl =
    v32TotalPnl ?? data.total_pnl ?? (data.realized_pnl ?? 0) + (data.unrealized_pnl ?? 0);

  return (
    <>
      {v32 ? (
        <Card className="mb-4 border-accent/30">
          <CardHeader>
            <CardTitle>{v32.label ?? t("home.live_sim_v32_title")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>{t("home.live_sim_v32_portfolio", { id: v32.portfolio ?? "P2" })}</p>
            <p>{t("home.live_sim_v32_risk", { pct: formatPercent(v32.risk_per_trade_pct ?? 0.25) })}</p>
            <p>
              {t("home.live_sim_v32_starting_model")}:{" "}
              {formatCurrency(v32.starting_model_equity_usd ?? data.starting_capital ?? 10000)}
            </p>
            {v32.observation_anchor ? (
              <p className="text-xs text-muted">
                {t("home.live_sim_v32_anchor")}: {v32.observation_anchor}
              </p>
            ) : null}
            <ul className="list-disc ps-5 text-muted">
              {v32.active_strategies?.map((s) => (
                <li key={`${s.symbol}-${s.timeframe}-${s.family}`}>
                  {s.display ?? s.family} · {s.symbol} · {s.timeframe} · {s.direction}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted">{t("home.live_sim_v32_ae_note")}</p>
            <p className="text-xs font-medium text-muted">{t("home.live_sim_v32_metrics_scope")}</p>
          </CardContent>
        </Card>
      ) : null}
      {/* Owner financial truth — primary */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <HomeSummaryCard label={t("home.live_sim_equity")}>
          <HomeSummaryValue>{formatCurrency(data.equity ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_total_pnl")}>
          <PnLDisplay value={displayTotalPnl} size="lg" />
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.realized_pnl")}>
          <PnLDisplay value={displayRealized} size="lg" />
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.unrealized_pnl")}>
          <PnLDisplay value={displayUnrealized} size="lg" />
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_open_count")}>
          <HomeSummaryValue>{openCount}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_closed_count")}>
          <HomeSummaryValue>{closedPositions}</HomeSummaryValue>
          {exitFills != null && exitFills !== closedPositions ? (
            <p className="text-xs text-muted mt-1">
              {t("home.live_sim_exit_fills_count")}: {exitFills}
            </p>
          ) : null}
        </HomeSummaryCard>
        {cleanWin && (cleanWin.closed_positions ?? 0) > 0 ? (
          <HomeSummaryCard label={t("home.live_sim_clean_window_pnl")}>
            <PnLDisplay value={cleanWin.net_pnl ?? 0} size="lg" />
            <p className="text-xs text-muted mt-1">
              {cleanWin.closed_positions} · PF {cleanWin.profit_factor ?? "—"}
            </p>
          </HomeSummaryCard>
        ) : null}
        <HomeSummaryCard label={t("home.gross_exposure")}>
          <HomeSummaryValue>{formatCurrency(data.gross_exposure ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_open_risk")}>
          <HomeSummaryValue>
            {formatCurrency(data.open_sl_risk_usd ?? 0)} ({formatPercent(data.open_sl_risk_pct ?? 0)})
          </HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_drawdown")}>
          <HomeSummaryValue>{formatPercent(data.current_drawdown_pct ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
      </div>

      {riskVis ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <HomeSummaryCard label={t("home.live_sim_risk_target_pct")}>
            <HomeSummaryValue>{formatPercent(riskVis.risk_target_pct ?? 0)}</HomeSummaryValue>
          </HomeSummaryCard>
          <HomeSummaryCard label={t("home.live_sim_avg_planned_risk_pct")}>
            <HomeSummaryValue>{formatPercent(riskVis.avg_planned_risk_pct ?? 0)}</HomeSummaryValue>
          </HomeSummaryCard>
          <HomeSummaryCard label={t("home.live_sim_capital_utilization")}>
            <HomeSummaryValue>{formatPercent(riskVis.capital_utilization_pct ?? 0)}</HomeSummaryValue>
          </HomeSummaryCard>
          <HomeSummaryCard label={t("home.live_sim_idle_capital")}>
            <HomeSummaryValue>{formatPercent(riskVis.idle_capital_pct ?? 0)}</HomeSummaryValue>
          </HomeSummaryCard>
        </div>
      ) : null}

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HomeSummaryCard label={t("home.live_sim_starting_capital")}>
          <HomeSummaryValue>{formatCurrency(data.starting_capital ?? 10000)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_cash")}>
          <HomeSummaryValue>{formatCurrency(data.cash ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.live_sim_available_margin")}>
          <HomeSummaryValue>{formatCurrency(data.available_margin ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label={t("home.total_return")}>
          <HomeSummaryValue>{formatPercent(data.total_return_pct ?? 0)}</HomeSummaryValue>
        </HomeSummaryCard>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.live_sim_open_positions")}</CardTitle></CardHeader>
        <CardContent>
          {openCount === 0 ? (
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
                      <td className="py-2">
                        {p.symbol}
                        {p.broker ? (
                          <span className="ms-1 text-xs text-muted">({p.broker.replace("live-sim-", "")})</span>
                        ) : null}
                      </td>
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
        <CardHeader><CardTitle>{t("home.live_sim_closed_trades")}</CardTitle></CardHeader>
        <CardContent>
          {(data.closed_trades?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted">{t("home.live_sim_no_closed_trades")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted">
                    <th className="py-2 text-start">{t("positions.asset")}</th>
                    <th className="py-2 text-start">{t("home.robot")}</th>
                    <th className="py-2 text-start">{t("positions.direction")}</th>
                    <th className="py-2 text-end">{t("positions.entry")}</th>
                    <th className="py-2 text-end">יציאה</th>
                    <th className="py-2 text-start">{t("home.live_sim_close_reason")}</th>
                    <th className="py-2 text-end">{t("home.live_sim_net_realized")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.closed_trades?.map((tr) => (
                    <tr key={tr.id} className="border-b border-border/50">
                      <td className="py-2">{tr.symbol}</td>
                      <td className="py-2">{tr.robot ?? tr.strategy_slug}</td>
                      <td className="py-2">{tr.direction}</td>
                      <td className="py-2 text-end font-mono">{tr.entry_price}</td>
                      <td className="py-2 text-end font-mono">{tr.exit_price ?? "—"}</td>
                      <td className="py-2">{tr.close_reason ?? "—"}</td>
                      <td className="py-2 text-end">
                        <PnLDisplay value={tr.net_realized_pnl} size="sm" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.live_sim_funnel_title")}</CardTitle></CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_candidates")}</p>
              <p className="font-mono text-2xl">{candidates.total}</p>
            </div>
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_passed")}</p>
              <p className="font-mono text-2xl">{candidates.passed_risk ?? candidates.accepted}</p>
            </div>
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_orders")}</p>
              <p className="font-mono text-2xl">{candidates.orders_sent ?? 0}</p>
            </div>
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_fills")}</p>
              <p className="font-mono text-2xl">{candidates.fills ?? 0}</p>
            </div>
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_opened")}</p>
              <p className="font-mono text-2xl">{candidates.positions_opened ?? 0}</p>
            </div>
            <div className="rounded-md border border-border/60 p-3">
              <p className="text-xs text-muted">{t("home.live_sim_funnel_rejected")}</p>
              <p className="font-mono text-2xl">{candidates.rejected}</p>
            </div>
          </div>
          <p className="mt-3 text-xs text-muted">{t("home.live_sim_accepted_not_executed_note")}</p>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.live_sim_recent_decisions")}</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2 text-right hover:bg-surface-inner-hover-soft"
            onClick={() => setDecisionsExpanded((open) => !open)}
            aria-expanded={decisionsExpanded}
          >
            <span className="font-medium">{decisionsToggleLabel}</span>
            <span className="shrink-0 text-xs text-accent">
              {decisionsExpanded ? t("home.live_sim_collapse_decisions") : t("home.live_sim_expand_decisions")}
            </span>
          </button>
          {decisionsExpanded ? (
            (data.recent_decisions?.length ?? 0) === 0 ? (
              <p className="text-sm text-muted">{t("home.no_decisions_new")}</p>
            ) : (
              <div className="max-h-[min(70vh,520px)] space-y-3 overflow-y-auto">
                {data.recent_decisions?.map((row) => (
                  <div key={row.id} className="rounded-md border border-border/60 p-3 text-sm">
                    <p className="font-medium">
                      {row.robot_label ?? row.strategy_slug} — {row.symbol} ({row.timeframe})
                    </p>
                    <p className={row.accepted && row.lifecycle_state !== "expired" && !row.metadata?.expired ? "text-success" : "text-warning"}>
                      {liveSimAllocationStatusLabel(row)}
                      {row.calculated_risk_usd != null ? ` · סיכון: $${row.calculated_risk_usd.toFixed(2)}` : ""}
                    </p>
                    {!row.accepted && row.rejection_reason_he ? (
                      <p className="text-muted">{t("home.rejection_reason")}: {row.rejection_reason_he}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            )
          ) : null}
        </CardContent>
      </Card>

      {data.broker_breakdown?.length ? (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("home.live_sim_broker_secondary")}</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            {data.broker_breakdown.map((b) => (
              <div key={b.slug} className="rounded-md border border-border/60 p-3 text-sm">
                <p className="font-medium">{b.label_he}</p>
                <p>{t("home.live_sim_cash")}: {formatCurrency(b.cash)}</p>
                <p>{t("home.live_sim_equity")}: {formatCurrency(b.equity)}</p>
                <p>{t("home.live_sim_available_margin")}: {formatCurrency(b.available_margin)}</p>
                <p>{t("home.realized_pnl")}: <PnLDisplay value={b.realized_pnl} size="sm" /></p>
                <p>{t("home.unrealized_pnl")}: <PnLDisplay value={b.unrealized_pnl} size="sm" /></p>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <LiveSimAllocationSettingsPanel />

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
    </>
  );
}
