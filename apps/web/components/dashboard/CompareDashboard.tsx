"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import type { LiveSimCompareSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatPercent } from "@/lib/utils";

type Props = {
  data: LiveSimCompareSummary | null;
  loading: boolean;
};

const METRICS: { key: keyof NonNullable<LiveSimCompareSummary["research"]>; labelKey: string; pct?: boolean }[] = [
  { key: "return_pct", labelKey: "home.total_return", pct: true },
  { key: "current_drawdown_pct", labelKey: "home.live_sim_drawdown", pct: true },
  { key: "win_rate_pct", labelKey: "home.win_rate", pct: true },
  { key: "closed_trades", labelKey: "home.closed_trades" },
  { key: "open_positions", labelKey: "home.open_positions" },
  { key: "sl_risk_pct", labelKey: "home.live_sim_sl_risk", pct: true },
  { key: "gross_exposure_pct", labelKey: "home.gross_exposure_pct", pct: true },
  { key: "realized_pnl", labelKey: "home.realized_pnl" },
  { key: "unrealized_pnl", labelKey: "home.unrealized_pnl" },
  { key: "fees_paid", labelKey: "home.fees_paid" },
];

function fmt(value: number | undefined, pct?: boolean) {
  if (value == null) return "—";
  return pct ? formatPercent(value) : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
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
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{data.research.label_he}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-muted">${data.research.starting_capital.toLocaleString()} → ${data.research.equity.toLocaleString()}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{data.live_sim.label_he}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-muted">${data.live_sim.starting_capital.toLocaleString()} → ${data.live_sim.equity.toLocaleString()}</p>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.normalized_metrics")}</CardTitle></CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-muted">
                <th className="py-2 text-start">{t("home.metric")}</th>
                <th className="py-2 text-end">{data.research.label_he}</th>
                <th className="py-2 text-end">{data.live_sim.label_he}</th>
              </tr>
            </thead>
            <tbody>
              {METRICS.map((m) => (
                <tr key={m.key} className="border-b border-border/50">
                  <td className="py-2">{t(m.labelKey)}</td>
                  <td className="py-2 text-end font-mono">{fmt(data.research![m.key] as number, m.pct)}</td>
                  <td className="py-2 text-end font-mono">{fmt(data.live_sim![m.key] as number, m.pct)}</td>
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
