"use client";

import { SectionPanel } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { t } from "@/lib/i18n";
import { formatRelativeTime } from "@/lib/utils";
import type { StrategyFreshness } from "@/lib/api-client";

function statusBadge(freshness: StrategyFreshness) {
  if (freshness.status === "error" || freshness.error) {
    return <Badge variant="danger">{t("home.strategy_error")}</Badge>;
  }
  if (freshness.stalled) {
    return <Badge variant="warning">{t("home.strategy_stalled")}</Badge>;
  }
  if (freshness.status === "paused") {
    return <Badge variant="warning">{t("home.strategy_degraded")}</Badge>;
  }
  if (freshness.healthy) {
    const historical = freshness.historical_backlog ?? freshness.backlog ?? 0;
    const live = freshness.live_backlog ?? 0;
    if (historical > 0 && live === 0) {
      return <Badge variant="success">{t("home.strategy_catching_up")}</Badge>;
    }
    return <Badge variant="success">{t("home.strategy_healthy")}</Badge>;
  }
  return <Badge variant="warning">{t("home.strategy_unhealthy")}</Badge>;
}

function MetricTile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border-nested bg-surface-inner p-3">
      <p className="text-xs text-muted">{label}</p>
      <div className="mt-1 font-mono text-sm text-financial">{children}</div>
    </div>
  );
}

export function StrategyFreshnessPanel({
  freshness,
}: {
  freshness: StrategyFreshness | null | undefined;
}) {
  if (!freshness) {
    return (
      <SectionPanel>
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-card-title">{t("home.strategy_freshness_title")}</h3>
        </div>
        <div className="rounded-md border border-border bg-surface p-4">
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        </div>
      </SectionPanel>
    );
  }

  const marketRows = Object.entries(freshness.market_candle_age_minutes ?? {});
  const liveBacklog = freshness.live_backlog ?? 0;
  const historicalBacklog =
    freshness.historical_backlog ?? freshness.backlog ?? 0;

  return (
    <SectionPanel>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-card-title">{t("home.strategy_freshness_title")}</h3>
        {statusBadge(freshness)}
      </div>
      <div className="space-y-3 rounded-md border border-border bg-surface p-4 text-sm">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <MetricTile label={t("home.strategy_last_evaluation")}>
            {freshness.last_evaluation_at
              ? formatRelativeTime(freshness.last_evaluation_at)
              : t("common.no_data")}
          </MetricTile>
          <MetricTile label={t("home.strategy_live_backlog")}>{liveBacklog}</MetricTile>
          <MetricTile label={t("home.strategy_historical_backlog")}>{historicalBacklog}</MetricTile>
        </div>
        {historicalBacklog > 0 && liveBacklog === 0 && freshness.healthy ? (
          <p className="text-xs text-muted">{t("home.strategy_historical_active_hint")}</p>
        ) : null}
        {marketRows.length ? (
          <div className="rounded-md border border-border-nested bg-surface-inner p-3">
            <p className="mb-2 text-xs text-muted">{t("home.market_candle_age")}</p>
            <ul className="space-y-1 font-mono text-xs text-text-normal">
              {marketRows.map(([sym, age]) => (
                <li key={sym}>
                  {sym}: {age != null ? `${age}m` : "—"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-muted">{t("home.strategy_backlog_hint")}</p>
      </div>
    </SectionPanel>
  );
}
