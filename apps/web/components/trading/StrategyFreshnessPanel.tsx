"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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

export function StrategyFreshnessPanel({
  freshness,
}: {
  freshness: StrategyFreshness | null | undefined;
}) {
  if (!freshness) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("home.strategy_freshness_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted">{t("common.no_data")}</p>
        </CardContent>
      </Card>
    );
  }

  const marketRows = Object.entries(freshness.market_candle_age_minutes ?? {});
  const liveBacklog = freshness.live_backlog ?? 0;
  const historicalBacklog =
    freshness.historical_backlog ?? freshness.backlog ?? 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{t("home.strategy_freshness_title")}</CardTitle>
        {statusBadge(freshness)}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <p className="text-muted">{t("home.strategy_last_evaluation")}</p>
            <p className="font-mono">
              {freshness.last_evaluation_at
                ? formatRelativeTime(freshness.last_evaluation_at)
                : t("common.no_data")}
            </p>
          </div>
          <div>
            <p className="text-muted">{t("home.strategy_live_backlog")}</p>
            <p className="font-mono">{liveBacklog}</p>
          </div>
          <div>
            <p className="text-muted">{t("home.strategy_historical_backlog")}</p>
            <p className="font-mono">{historicalBacklog}</p>
          </div>
        </div>
        {historicalBacklog > 0 && liveBacklog === 0 && freshness.healthy ? (
          <p className="text-xs text-muted">{t("home.strategy_historical_active_hint")}</p>
        ) : null}
        {marketRows.length ? (
          <div>
            <p className="mb-1 text-muted">{t("home.market_candle_age")}</p>
            <ul className="space-y-1 font-mono text-xs">
              {marketRows.map(([sym, age]) => (
                <li key={sym}>
                  {sym}: {age != null ? `${age}m` : "—"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-muted">{t("home.strategy_backlog_hint")}</p>
      </CardContent>
    </Card>
  );
}
