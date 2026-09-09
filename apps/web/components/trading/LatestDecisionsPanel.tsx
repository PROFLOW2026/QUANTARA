"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ExpandableText } from "@/components/ui/ExpandableText";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import {
  isEntrySignalDecision,
  translateRobotStrategyLabel,
  translateSignalReason,
  translateTimeframe,
} from "@/lib/display-text";
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

function yesNo(value: boolean | undefined) {
  return value ? t("common.yes") : t("common.no");
}

export function LatestDecisionsPanel({
  decisions,
  timeframe,
}: {
  decisions: Decision[];
  timeframe: string;
}) {
  const sorted = [...decisions].sort((a, b) => {
    const robot = (a.robot_label ?? "").localeCompare(b.robot_label ?? "");
    if (robot !== 0) return robot;
    return (a.instrument ?? "").localeCompare(b.instrument ?? "");
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.latest_decisions_title")}</CardTitle>
        <p className="text-xs text-muted">
          {t("home.latest_decisions_hint")} · {translateTimeframe(timeframe)}
        </p>
      </CardHeader>
      <CardContent>
        {!sorted.length ? (
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        ) : (
          <>
          <div className="space-y-3 md:hidden">
            {sorted.map((row) => {
              const entrySignal =
                row.entry_signal ??
                (isEntrySignalDecision(row.decision_type) || row.trade_opened);
              return (
                <div
                  key={`mobile-${row.robot_label ?? "na"}-${row.instrument}-${row.id}`}
                  className="rounded-md border border-border/60 p-3 text-sm"
                >
                  <p className="text-xs text-muted">
                    {translateRobotStrategyLabel(
                      row.robot_label,
                      row.strategy_name,
                      row.strategy_slug
                    )}
                  </p>
                  <p className="mt-1 font-medium">{row.instrument ?? "—"}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <DecisionTypeBadge type={row.decision_type} />
                    {freshnessBadge(row.fresh)}
                  </div>
                  <p className="mt-2 text-xs text-muted">
                    {formatRelativeTime(row.timestamp)} · {translateTimeframe(row.timeframe ?? timeframe)}
                  </p>
                  <div className="mt-2">
                    <ExpandableText text={translateSignalReason(row.message)} />
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                    <p>{t("home.entry_signal")}: {yesNo(entrySignal)}</p>
                    <p>{t("home.position_open_now")}: {yesNo(row.position_open)}</p>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-[880px] text-sm">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="py-2 text-right">{t("home.robot_strategy")}</th>
                <th className="py-2 text-right">{t("home.asset_symbol")}</th>
                <th className="py-2 text-right">{t("home.timeframe")}</th>
                <th className="py-2 text-right">{t("home.latest_decision")}</th>
                <th className="py-2 text-right">{t("home.decision_time")}</th>
                <th className="py-2 text-right">{t("home.data_freshness")}</th>
                <th className="py-2 text-right min-w-[180px]">{t("home.reason")}</th>
                <th className="py-2 text-right">{t("home.entry_signal")}</th>
                <th className="py-2 text-right">{t("home.position_open_now")}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const entrySignal =
                  row.entry_signal ??
                  (isEntrySignalDecision(row.decision_type) || row.trade_opened);
                return (
                  <tr
                    key={`${row.robot_label ?? "na"}-${row.instrument}-${row.id}`}
                    className="border-b border-border/50 align-top"
                  >
                    <td className="py-2 text-xs text-muted">
                      {translateRobotStrategyLabel(
                        row.robot_label,
                        row.strategy_name,
                        row.strategy_slug
                      )}
                    </td>
                    <td className="py-2 font-medium">{row.instrument ?? "—"}</td>
                    <td className="py-2">{translateTimeframe(row.timeframe ?? timeframe)}</td>
                    <td className="py-2">
                      <DecisionTypeBadge type={row.decision_type} />
                    </td>
                    <td className="py-2 text-muted">{formatRelativeTime(row.timestamp)}</td>
                    <td className="py-2">{freshnessBadge(row.fresh)}</td>
                    <td className="py-2 max-w-[280px]">
                      <ExpandableText text={translateSignalReason(row.message)} />
                    </td>
                    <td className="py-2">{yesNo(entrySignal)}</td>
                    <td className="py-2">{yesNo(row.position_open)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
