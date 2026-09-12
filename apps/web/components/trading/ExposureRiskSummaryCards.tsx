"use client";

import { Card, CardContent, CardHeader, CardTitle, HighlightCard } from "@/components/ui/card";
import { FinancialValue } from "@/components/trading/FinancialValue";
import type { AssetAnalyticsSummary } from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatRiskPercentOrUnavailable,
} from "@/lib/display-text";
import { formatRiskRewardLabel, formatTargetProfitOrUnavailable } from "@/lib/profit-target";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

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

function SummaryMetricCard({
  title,
  titleHint,
  loading,
  children,
  highlight = false,
}: {
  title: string;
  titleHint?: string;
  loading?: boolean;
  children: React.ReactNode;
  highlight?: boolean;
}) {
  const Wrapper = highlight ? HighlightCard : Card;
  return (
    <Wrapper className="flex min-h-[7.25rem] flex-col">
      <CardHeader className="mb-2 shrink-0">
        <CardTitle title={titleHint}>{title}</CardTitle>
      </CardHeader>
      <CardContent className="mt-auto flex min-h-[2.25rem] items-end pb-0.5">
        {loading ? (
          <span className="text-sm text-muted">{t("common.loading")}</span>
        ) : (
          children
        )}
      </CardContent>
    </Wrapper>
  );
}

export function CombinedRiskRewardSummaryCard({
  summary,
  loading,
}: {
  summary: AssetAnalyticsSummary | null | undefined;
  loading?: boolean;
}) {
  const hasOpenPositions = (summary?.open_position_count ?? 0) > 0;
  const combinedRrLabel =
    summary?.combined_risk_reward != null
      ? formatRiskRewardLabel(summary.combined_risk_reward)
      : hasOpenPositions
        ? t("home.risk_reward_unavailable")
        : "—";

  return (
    <SummaryMetricCard title={t("home.combined_risk_reward_title")} loading={loading}>
      {hasOpenPositions && summary?.combined_risk_reward == null ? (
        <p className="text-[clamp(0.6875rem,0.75vw+0.4rem,0.8125rem)] leading-snug text-financial">
          {combinedRrLabel}
        </p>
      ) : (
        <FinancialValue className="w-full">{combinedRrLabel}</FinancialValue>
      )}
    </SummaryMetricCard>
  );
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

  return (
    <div className="grid auto-rows-fr gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <SummaryMetricCard
        highlight
        title={t("home.open_exposure_title")}
        titleHint={t("home.open_exposure_usd_hint")}
        loading={loading}
      >
        <FinancialValue className={cn("w-full")}>{formatExposureMetric(summary, "open_exposure")}</FinancialValue>
      </SummaryMetricCard>
      <SummaryMetricCard highlight title={t("home.open_risk_sl_title")} loading={loading}>
        <FinancialValue className="w-full">{formatExposureMetric(summary, "open_risk_usd")}</FinancialValue>
      </SummaryMetricCard>
      <SummaryMetricCard title={t("home.open_risk_pct_title")} loading={loading}>
        <FinancialValue className="w-full">{formatExposureMetric(summary, "open_risk_pct")}</FinancialValue>
      </SummaryMetricCard>
      <SummaryMetricCard
        title={t("home.remaining_sl_risk_title")}
        titleHint={t("home.remaining_sl_risk_hint")}
        loading={loading}
      >
        <FinancialValue className="w-full">{formatRemainingMetric(summary, "remaining_sl_risk_usd")}</FinancialValue>
      </SummaryMetricCard>
      <SummaryMetricCard
        title={t("home.projected_equity_at_stops_title")}
        titleHint={t("home.projected_equity_at_stops_hint")}
        loading={loading}
      >
        <FinancialValue className="w-full">
          {formatRemainingMetric(summary, "projected_equity_at_stops")}
        </FinancialValue>
      </SummaryMetricCard>
      <SummaryMetricCard title={t("home.target_profit_total_title")} loading={loading}>
        {hasOpenPositions && summary?.open_target_profit_usd == null ? (
          <p className="text-[clamp(0.6875rem,0.75vw+0.4rem,0.8125rem)] leading-snug text-financial">
            {targetProfitLabel}
          </p>
        ) : (
          <FinancialValue className="w-full">{targetProfitLabel}</FinancialValue>
        )}
      </SummaryMetricCard>
    </div>
  );
}
