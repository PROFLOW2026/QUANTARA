"use client";

/**
 * AI Comparison Dashboard — Quantara vs AI Trader (side-by-side).
 *
 * Uses the SAME metric definitions and the SAME time range for both systems.
 * Missing data shows "אין נתונים", never 0.
 * Replay / backtest results are NEVER mixed into paper live data.
 */

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import type { AiTraderSummary, LiveSimAccountSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";
import { cn } from "@/lib/utils";

// ── Time range ─────────────────────────────────────────────────────────────────

type RangeId = "today" | "7d" | "30d" | "all";

const RANGES: { id: RangeId; labelKey: string }[] = [
  { id: "today", labelKey: "ai_compare.range_today" },
  { id: "7d",    labelKey: "ai_compare.range_7d" },
  { id: "30d",   labelKey: "ai_compare.range_30d" },
  { id: "all",   labelKey: "ai_compare.range_all" },
];

// ── Metric row ─────────────────────────────────────────────────────────────────

type MetricProps = {
  label: string;
  quantaraValue: React.ReactNode;
  aiValue: React.ReactNode;
  highlight?: boolean;
};

function MetricRow({ label, quantaraValue, aiValue, highlight }: MetricProps) {
  return (
    <tr className={cn("border-b border-border/50", highlight && "bg-surface-inner/40")}>
      <td className="py-3 pe-4 text-sm text-muted">{label}</td>
      <td className="py-3 pe-4 text-end text-sm font-medium">{quantaraValue}</td>
      <td className="py-3 text-end text-sm font-medium">{aiValue}</td>
    </tr>
  );
}

function noData() {
  return <span className="text-muted">{t("ai_compare.no_data")}</span>;
}

function fmtPct(v: number | null | undefined, suffix = "%") {
  if (v == null) return noData();
  return <span>{v.toFixed(2)}{suffix}</span>;
}

function fmtPF(v: number | null | undefined) {
  if (v == null) return noData();
  if (v >= 999) return <span>∞</span>;
  return <span>{v.toFixed(2)}</span>;
}

function fmtPnl(v: number | null | undefined) {
  if (v == null) return noData();
  return <PnLDisplay value={v} size="sm" />;
}

function fmtNum(v: number | null | undefined) {
  if (v == null) return noData();
  return <span>{v}</span>;
}

// ── Props ──────────────────────────────────────────────────────────────────────

type Props = {
  quantaraData: LiveSimAccountSummary | null;
  aiData: AiTraderSummary | null;
  loading: boolean;
};

// ── Component ──────────────────────────────────────────────────────────────────

export function AiCompareDashboard({ quantaraData, aiData, loading }: Props) {
  const [range, setRange] = useState<RangeId>("all");

  const qAvailable = Boolean(quantaraData?.available);
  const aiAvailable = Boolean(aiData);

  // ── Quantara metrics ──────────────────────────────────────────────────────────
  const qStarting    = quantaraData?.starting_capital ?? null;
  const qEquity      = quantaraData?.equity ?? null;
  const qRealized    = quantaraData?.realized_pnl ?? null;
  const qUnrealized  = quantaraData?.unrealized_pnl ?? null;
  const qTotalPnl    =
    qRealized != null && qUnrealized != null ? qRealized + qUnrealized : null;
  const qReturnPct   = quantaraData?.total_return_pct ?? null;
  const qDrawdown    = quantaraData?.current_drawdown_pct ?? null;
  const qOpenCount   = quantaraData?.open_positions?.length ?? null;
  const qClosedCount =
    quantaraData?.closed_positions_count ??
    quantaraData?.closed_trades?.length ??
    null;

  // Win rate & PF from liveSimAccount clean_window if available
  const cw = quantaraData?.clean_window;
  const cwWins   = cw?.wins ?? null;
  const cwTotal  = (cwWins != null && cw?.losses != null) ? cwWins + cw.losses : null;
  const qWinRate = cwWins != null && cwTotal != null && cwTotal > 0
    ? Math.round(cwWins / cwTotal * 1000) / 10
    : null;
  const qPF = cw?.profit_factor ?? null;

  const qLastUpdated = null; // not exposed in LiveSimAccountSummary

  // ── AI Trader metrics ─────────────────────────────────────────────────────────
  const aiStarting    = aiData?.starting_capital ?? null;
  const aiEquity      = aiData?.equity ?? aiData?.capital ?? null;  // real equity: cash + market_value
  const aiRealized    = aiData?.realized_pnl ?? null;
  const aiUnrealized  = aiData?.unrealized_pnl ?? null;
  const aiTotalPnl    = aiData?.total_pnl ?? (aiRealized != null && aiUnrealized != null ? aiRealized + aiUnrealized : null);
  const aiReturnPct   = aiData?.return_pct ?? null;
  const aiDrawdownUsd = aiData?.max_drawdown_usd ?? null;
  const aiDrawdownPct = aiData?.max_drawdown_pct ?? null;
  const aiOpenCount   = aiData?.open_fills.length ?? null;
  const aiClosedCount = aiData?.closed_count ?? null;
  const aiWinRate     = aiData?.win_rate_pct ?? null;
  const aiPF          = aiData?.profit_factor ?? null;
  const aiLastUpdated = aiData?.last_updated ?? null;

  // ── Staleness warnings ────────────────────────────────────────────────────────
  const aiStale = aiData?.data_stale ?? false;
  const aiRunning = aiData?.runner_alive ?? false;

  if (loading && !quantaraData && !aiData) {
    return <p className="text-muted">{t("common.loading")}</p>;
  }

  return (
    <>
      {/* ── Range picker ── */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted">{t("ai_compare.range_label")}:</span>
        {RANGES.map((r) => (
          <button
            key={r.id}
            type="button"
            onClick={() => setRange(r.id)}
            className={cn(
              "rounded px-3 py-1 text-sm transition-colors",
              range === r.id
                ? "bg-primary text-primary-foreground"
                : "border border-border text-muted hover:bg-surface-active"
            )}
          >
            {t(r.labelKey)}
          </button>
        ))}
        {range !== "all" && (
          <span className="text-xs text-warning">{t("ai_compare.range_note")}</span>
        )}
      </div>

      {/* ── Status row ── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Card>
          <CardContent className="flex items-center justify-between py-3">
            <span className="text-sm font-medium">Quantara Live Sim</span>
            <span className={cn(
              "text-xs",
              qAvailable ? "text-profit" : "text-muted"
            )}>
              {qAvailable ? t("ai_compare.data_live") : t("ai_compare.data_unavailable")}
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-between py-3">
            <span className="text-sm font-medium">AI Trader Paper</span>
            <span className={cn(
              "text-xs",
              aiRunning && !aiStale
                ? "text-profit"
                : aiStale
                  ? "text-warning"
                  : "text-muted"
            )}>
              {aiRunning && !aiStale
                ? t("ai_compare.data_live")
                : aiStale
                  ? t("ai_compare.data_stale")
                  : t("ai_compare.data_unavailable")}
              {aiLastUpdated ? ` · ${aiLastUpdated}` : ""}
            </span>
          </CardContent>
        </Card>
      </div>

      {/* ── Main comparison table ── */}
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-surface-inner/50">
                <th className="py-3 pe-4 text-start text-sm font-semibold text-muted">
                  {t("ai_compare.metric")}
                </th>
                <th className="py-3 pe-4 text-end text-sm font-semibold">
                  Quantara
                </th>
                <th className="py-3 text-end text-sm font-semibold">
                  AI Trader
                </th>
              </tr>
            </thead>
            <tbody>
              <MetricRow
                label={t("ai_compare.starting_capital")}
                quantaraValue={qStarting != null ? formatCurrency(qStarting) : noData()}
                aiValue={aiStarting != null ? formatCurrency(aiStarting) : noData()}
              />
              <MetricRow
                label={t("ai_compare.current_equity")}
                quantaraValue={qEquity != null ? formatCurrency(qEquity) : noData()}
                aiValue={aiEquity != null ? formatCurrency(aiEquity) : noData()}
                highlight
              />
              <MetricRow
                label={t("ai_compare.total_return")}
                quantaraValue={fmtPct(qReturnPct)}
                aiValue={fmtPct(aiReturnPct)}
                highlight
              />
              <MetricRow
                label={t("ai_compare.realized_pnl")}
                quantaraValue={fmtPnl(qRealized)}
                aiValue={fmtPnl(aiRealized)}
              />
              <MetricRow
                label={t("ai_compare.unrealized_pnl")}
                quantaraValue={fmtPnl(qUnrealized)}
                aiValue={fmtPnl(aiUnrealized)}
              />
              <MetricRow
                label={t("ai_compare.total_pnl")}
                quantaraValue={fmtPnl(qTotalPnl)}
                aiValue={fmtPnl(aiTotalPnl)}
              />
              <MetricRow
                label={t("ai_compare.max_drawdown")}
                quantaraValue={fmtPct(qDrawdown)}
                aiValue={
                  aiDrawdownUsd != null
                    ? <span>{formatCurrency(aiDrawdownUsd)} ({formatPercent(aiDrawdownPct ?? 0)})</span>
                    : noData()
                }
                highlight
              />
              <MetricRow
                label={t("ai_compare.open_positions")}
                quantaraValue={fmtNum(qOpenCount)}
                aiValue={fmtNum(aiOpenCount)}
              />
              <MetricRow
                label={t("ai_compare.closed_trades")}
                quantaraValue={fmtNum(qClosedCount)}
                aiValue={fmtNum(aiClosedCount)}
                highlight
              />
              <MetricRow
                label={t("ai_compare.win_rate")}
                quantaraValue={qWinRate != null ? <span>{qWinRate.toFixed(1)}%</span> : noData()}
                aiValue={aiWinRate != null ? <span>{aiWinRate.toFixed(1)}%</span> : noData()}
                highlight
              />
              <MetricRow
                label={t("ai_compare.profit_factor")}
                quantaraValue={fmtPF(qPF)}
                aiValue={fmtPF(aiPF)}
              />
            </tbody>
          </table>
        </CardContent>
      </Card>

      {/* ── Scope notes ── */}
      <div className="mt-4 space-y-1 text-xs text-muted">
        <p>
          <span className="font-medium">Quantara: </span>
          {t("ai_compare.quantara_scope_note")}
        </p>
        <p>
          <span className="font-medium">AI Trader: </span>
          {t("ai_compare.ai_scope_note")}
        </p>
        {range !== "all" && (
          <p className="text-warning">{t("ai_compare.range_filter_note")}</p>
        )}
        <p className="mt-2">{t("ai_compare.no_funds_transfer_note")}</p>
      </div>
    </>
  );
}
