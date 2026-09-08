"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import { translateSignalReason, translateTimeframe } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatRelativeTime } from "@/lib/utils";
import type { Decision } from "@/lib/api-client";

function freshnessBadge(fresh?: boolean) {
  if (fresh == null) return null;
  return (
    <Badge variant={fresh ? "success" : "warning"}>
      {fresh ? t("home.decision_fresh") : t("home.decision_stale")}
    </Badge>
  );
}

export function LatestDecisionsPanel({
  decisions,
  timeframe,
}: {
  decisions: Decision[];
  timeframe: string;
}) {
  const sorted = [...decisions].sort((a, b) =>
    (a.instrument ?? "").localeCompare(b.instrument ?? "")
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.latest_decisions_title")}</CardTitle>
        <p className="text-xs text-muted">
          {t("home.latest_decisions_hint")} · {translateTimeframe(timeframe)}
        </p>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {!sorted.length ? (
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        ) : (
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 text-right">{t("home.asset_symbol")}</th>
                <th className="py-2 text-right">{t("home.timeframe")}</th>
                <th className="py-2 text-right">{t("home.latest_decision")}</th>
                <th className="py-2 text-right">{t("home.decision_time")}</th>
                <th className="py-2 text-right">{t("home.data_freshness")}</th>
                <th className="py-2 text-right">{t("home.reason")}</th>
                <th className="py-2 text-right">{t("home.trade_opened")}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={`${row.instrument}-${row.id}`} className="border-b border-border/50">
                  <td className="py-2 font-medium">{row.instrument ?? "—"}</td>
                  <td className="py-2">{translateTimeframe(row.timeframe ?? timeframe)}</td>
                  <td className="py-2">
                    <DecisionTypeBadge type={row.decision_type} />
                  </td>
                  <td className="py-2 text-muted">{formatRelativeTime(row.timestamp)}</td>
                  <td className="py-2">{freshnessBadge(row.fresh)}</td>
                  <td className="py-2 max-w-[240px] truncate" title={row.message}>
                    {translateSignalReason(row.message)}
                  </td>
                  <td className="py-2">
                    {row.trade_opened ? t("common.yes") : t("common.no")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
