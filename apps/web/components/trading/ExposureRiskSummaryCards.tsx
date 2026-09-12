"use client";

import { Card, CardContent, CardHeader, CardTitle, HighlightCard } from "@/components/ui/card";
import type { AssetAnalyticsSummary } from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatRiskPercentOrUnavailable,
} from "@/lib/display-text";
import { formatRiskRewardLabel, formatTargetProfitOrUnavailable } from "@/lib/profit-target";
import { t } from "@/lib/i18n";

function formatExposureMetric(
  summary: AssetAnalyticsSummary | null | undefined,
  field: "open_exposure" | "open_risk_usd" | "open_risk_pct"
) {
  if (!summary) {
    return t("common.metric_unavailable");
  }
  const value = summary[field];
  if (value != null) {
    return field === "open_risk_pct"
      ? formatRiskPercentOrUnavailable(value)
      : formatCurrencyOrUnavailable(value);
  }
  if ((summary.open_position_count ?? 0) === 0) {
    return field === "open_risk_pct"
      ? formatRiskPercentOrUnavailable(0)
      : formatCurrencyOrUnavailable(0);
  }
  return t("common.metric_unavailable");
}

function formatRemainingMetric(
  summary: AssetAnalyticsSummary | null | undefined,
  field: "remaining_sl_risk_usd" | "projected_equity_at_stops"
) {
  if (!summary) {
    return t("common.metric_unavailable");
  }
  const value = summary[field];
  if (value != null) {
    return formatCurrencyOrUnavailable(value);
  }
  if ((summary.open_position_count ?? 0) === 0) {
    return field === "remaining_sl_risk_usd"
      ? formatCurrencyOrUnavailable(0)
      : formatCurrencyOrUnavailable(summary.total_equity ?? 0);
  }
  return t("common.metric_unavailable");
}

export function ExposureRiskSummaryCards({
  summary,
  loading,
}: {
  summary: AssetAnalyticsSummary | null | undefined;
  loading?: boolean;
}) {
  const hasOpenPositions = (summary?.open_position_count ?? 0) > 0;
  const targetProfitLabel =
    summary?.open_target_profit_usd != null
      ? formatTargetProfitOrUnavailable(summary.open_target_profit_usd)
      : hasOpenPositions
        ? t("home.target_profit_partial")
        : formatTargetProfitOrUnavailable(0);
  const combinedRrLabel =
    summary?.combined_risk_reward != null
      ? formatRiskRewardLabel(summary.combined_risk_reward)
      : hasOpenPositions
        ? t("home.risk_reward_unavailable")
        : "—";

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
      <HighlightCard>
        <CardHeader>
          <CardTitle title={t("home.open_exposure_usd_hint")}>
            {t("home.open_exposure_title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl text-financial">{formatExposureMetric(summary, "open_exposure")}</p>
          )}
        </CardContent>
      </HighlightCard>
      <HighlightCard>
        <CardHeader>
          <CardTitle>{t("home.open_risk_sl_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl text-financial">{formatExposureMetric(summary, "open_risk_usd")}</p>
          )}
        </CardContent>
      </HighlightCard>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.open_risk_pct_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">{formatExposureMetric(summary, "open_risk_pct")}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle title={t("home.remaining_sl_risk_hint")}>
            {t("home.remaining_sl_risk_title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">{formatRemainingMetric(summary, "remaining_sl_risk_usd")}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle title={t("home.projected_equity_at_stops_hint")}>
            {t("home.projected_equity_at_stops_title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">
              {formatRemainingMetric(summary, "projected_equity_at_stops")}
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.target_profit_total_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl text-financial">{targetProfitLabel}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.combined_risk_reward_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">{combinedRrLabel}</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
