"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssetAnalyticsSummary } from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatPercentOrUnavailable,
} from "@/lib/display-text";
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
      ? formatPercentOrUnavailable(value)
      : formatCurrencyOrUnavailable(value);
  }
  if ((summary.open_position_count ?? 0) === 0) {
    return field === "open_risk_pct"
      ? formatPercentOrUnavailable(0)
      : formatCurrencyOrUnavailable(0);
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
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle title={t("home.open_exposure_usd_hint")}>
            {t("home.open_exposure_title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">{formatExposureMetric(summary, "open_exposure")}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.open_risk_sl_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">{formatExposureMetric(summary, "open_risk_usd")}</p>
          )}
        </CardContent>
      </Card>
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
    </div>
  );
}
