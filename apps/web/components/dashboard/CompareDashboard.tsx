"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import type { LiveSimCompareSide, LiveSimCompareSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatPercent } from "@/lib/utils";

type Props = {
  data: LiveSimCompareSummary | null;
  loading: boolean;
};

const METRICS: {
  key: keyof Pick<
    LiveSimCompareSide,
    | "return_pct"
    | "current_drawdown_pct"
    | "win_rate_pct"
    | "closed_trades"
    | "open_positions"
    | "sl_risk_pct"
    | "gross_exposure_pct"
    | "realized_pnl"
    | "unrealized_pnl"
    | "fees_paid"
  >;
  labelKey: string;
  pct?: boolean;
  nullable?: boolean;
}[] = [
  { key: "return_pct", labelKey: "home.total_return", pct: true },
  { key: "current_drawdown_pct", labelKey: "home.live_sim_drawdown", pct: true },
  { key: "win_rate_pct", labelKey: "home.win_rate", pct: true, nullable: true },
  { key: "closed_trades", labelKey: "home.closed_trades" },
  { key: "open_positions", labelKey: "home.open_positions" },
  { key: "sl_risk_pct", labelKey: "home.live_sim_sl_risk", pct: true, nullable: true },
  { key: "gross_exposure_pct", labelKey: "home.gross_exposure_pct", pct: true },
  { key: "realized_pnl", labelKey: "home.realized_pnl" },
  { key: "unrealized_pnl", labelKey: "home.unrealized_pnl" },
  { key: "fees_paid", labelKey: "home.fees_paid" },
];

function fmtMetric(
  side: LiveSimCompareSide,
  key: (typeof METRICS)[number]["key"],
  pct?: boolean,
  nullable?: boolean,
) {
  if (key === "sl_risk_pct" && side.sl_risk_pct == null) {
    return side.sl_risk_unavailable_he ?? t("home.compare_metric_unavailable");
  }
  const value = side[key];
  if (nullable && (value == null || (key === "win_rate_pct" && side.closed_trades === 0))) {
    return t("home.compare_metric_not_applicable");
  }
  if (value == null) return t("home.compare_metric_unavailable");
  return pct ? formatPercent(value as number) : (value as number).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ScopeBreakdown({ side }: { side: LiveSimCompareSide }) {
  const strategy = side.scopes?.strategy;
  const broker = side.scopes?.broker;
  if (!strategy && !broker) return null;

  return (
    <div className="mt-3 grid gap-2 border-t border-border/50 pt-3 text-xs text-muted sm:grid-cols-2">
      {strategy ? (
        <div>
          <p className="font-medium text-foreground">{strategy.label_he}</p>
          <p>
            {t("home.closed_trades")}: {strategy.closed_trades_count ?? "—"}
            {" · "}
            {t("home.open_positions")}: {strategy.open_positions ?? "—"}
          </p>
          <p>
            {t("home.live_sim_sl_risk")}:{" "}
            {strategy.open_sl_risk_pct != null
              ? formatPercent(strategy.open_sl_risk_pct)
              : strategy.open_sl_risk_unavailable_he ?? t("home.compare_metric_unavailable")}
          </p>
        </div>
      ) : null}
      {broker ? (
        <div>
          <p className="font-medium text-foreground">{broker.label_he}</p>
          <p>
            {t("home.open_positions")}: {broker.open_positions ?? "—"}
            {broker.unique_symbols_open != null ? ` (${broker.unique_symbols_open} ${t("home.compare_unique_symbols")})` : ""}
          </p>
          <p>
            {t("home.live_sim_sl_risk")}:{" "}
            {broker.open_sl_risk_pct != null
              ? formatPercent(broker.open_sl_risk_pct)
              : broker.open_sl_risk_unavailable_he ?? t("home.compare_metric_unavailable")}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function CompareDashboard({ data, loading }: Props) {
  if (loading && !data) {
    return <p className="text-muted">{t("common.loading")}</p>;
  }
  if (!data?.available || !data.research || !data.live_sim) {
    return <p className="text-muted">{t("common.section_unavailable")}</p>;
  }

  return (
    <>
      <div className="mb-2">
        <h2 className="text-lg font-semibold">{t("home.compare_title")}</h2>
        <p className="text-sm text-muted">{t("home.compare_subtitle")}</p>
        {data.scope_notes_he ? (
          <div className="mt-2 space-y-1 text-xs text-muted">
            <p>{data.scope_notes_he.financial}</p>
            <p>{data.scope_notes_he.trade_stats}</p>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{data.research.label_he}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-muted">
              ${data.research.starting_capital.toLocaleString()} → ${data.research.equity.toLocaleString()}
            </p>
            <p className="mt-1 text-xs text-muted">{data.research.financial_scope_he}</p>
            <ScopeBreakdown side={data.research} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{data.live_sim.label_he}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-muted">
              ${data.live_sim.starting_capital.toLocaleString()} → ${data.live_sim.equity.toLocaleString()}
            </p>
            <p className="mt-1 text-xs text-muted">{data.live_sim.financial_scope_he}</p>
            <ScopeBreakdown side={data.live_sim} />
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.normalized_metrics")}</CardTitle></CardHeader>
        <CardContent className="overflow-x-auto">
          <p className="mb-3 text-xs text-muted">{t("home.compare_trade_stats_scope_hint")}</p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-muted">
                <th className="py-2 text-start">{t("home.metric")}</th>
                <th className="py-2 text-end">
                  <div>{data.research.label_he}</div>
                  <div className="text-xs font-normal">{data.research.trade_stats_scope_he}</div>
                </th>
                <th className="py-2 text-end">
                  <div>{data.live_sim.label_he}</div>
                  <div className="text-xs font-normal">{data.live_sim.trade_stats_scope_he}</div>
                </th>
              </tr>
            </thead>
            <tbody>
              {METRICS.map((m) => (
                <tr key={m.key} className="border-b border-border/50">
                  <td className="py-2">{t(m.labelKey)}</td>
                  <td className="py-2 text-end font-mono">
                    {fmtMetric(data.research!, m.key, m.pct, m.nullable)}
                  </td>
                  <td className="py-2 text-end font-mono">
                    {fmtMetric(data.live_sim!, m.key, m.pct, m.nullable)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.candidates_seen")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">{data.live_sim.candidates_total ?? 0}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.candidates_accepted")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">{data.live_sim.candidates_accepted ?? 0}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.candidates_rejected")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">{data.live_sim.candidates_rejected ?? 0}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.acceptance_rate")}</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-2xl">{formatPercent(data.live_sim.acceptance_rate_pct ?? 0)}</p></CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{t("home.research_realized")}</CardTitle></CardHeader>
          <CardContent><PnLDisplay value={data.research.realized_pnl} size="lg" /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.live_sim_realized")}</CardTitle></CardHeader>
          <CardContent><PnLDisplay value={data.live_sim.realized_pnl} size="lg" /></CardContent>
        </Card>
      </div>
    </>
  );
}
