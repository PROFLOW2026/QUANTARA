"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssetAnalyticsSummary } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";

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
          <CardTitle>{t("home.open_exposure_title")}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <span className="text-muted">{t("common.loading")}</span>
          ) : (
            <p className="font-mono text-2xl">
              {formatCurrency(summary?.open_exposure ?? 0)}
            </p>
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
            <p className="font-mono text-2xl">
              {formatCurrency(summary?.open_risk_usd ?? 0)}
            </p>
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
            <p className="font-mono text-2xl">
              {formatPercent(summary?.open_risk_pct ?? 0)}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
