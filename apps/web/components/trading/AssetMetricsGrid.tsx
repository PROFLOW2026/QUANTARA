"use client";

import type { AssetAnalyticsRow } from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatRiskPercentOrUnavailable,
} from "@/lib/display-text";
import { formatRiskRewardLabel, formatTargetProfitOrUnavailable } from "@/lib/profit-target";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { FinancialValue } from "@/components/trading/FinancialValue";
import { t } from "@/lib/i18n";
import { cn, formatRiskPercent } from "@/lib/utils";

function hasOpenPositions(asset: AssetAnalyticsRow) {
  return (asset.open_positions ?? 0) > 0;
}

function formatAssetExposure(asset: AssetAnalyticsRow) {
  if (asset.open_exposure != null) {
    return formatCurrencyOrUnavailable(asset.open_exposure);
  }
  if (!hasOpenPositions(asset)) {
    return formatCurrencyOrUnavailable(0);
  }
  return formatCurrencyOrUnavailable(null);
}

function formatAssetRisk(asset: AssetAnalyticsRow) {
  if (asset.open_risk_usd != null) {
    return formatCurrencyOrUnavailable(asset.open_risk_usd);
  }
  if (!hasOpenPositions(asset)) {
    return formatCurrencyOrUnavailable(0);
  }
  return formatCurrencyOrUnavailable(null);
}

function formatAssetTargetProfit(asset: AssetAnalyticsRow) {
  if (!hasOpenPositions(asset)) {
    return formatCurrencyOrUnavailable(0);
  }
  if (asset.open_target_profit_usd != null) {
    return formatTargetProfitOrUnavailable(asset.open_target_profit_usd);
  }
  return formatTargetProfitOrUnavailable(null);
}

function formatAssetRiskReward(asset: AssetAnalyticsRow) {
  if (!hasOpenPositions(asset)) {
    return "—";
  }
  return formatRiskRewardLabel(asset.combined_risk_reward);
}

function formatCurrentRiskPct(asset: AssetAnalyticsRow) {
  if (asset.open_risk_pct != null) {
    return formatRiskPercentOrUnavailable(asset.open_risk_pct);
  }
  if (!hasOpenPositions(asset)) {
    return formatRiskPercentOrUnavailable(0);
  }
  return formatRiskPercentOrUnavailable(null);
}

function MetricCell({
  label,
  value,
  className,
}: {
  label: string;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "min-h-[2.625rem] rounded-md border border-border-nested bg-surface-inner px-2 py-1.5",
        className
      )}
    >
      <p className="truncate text-muted">{label}</p>
      <div className="mt-0.5 min-h-[1rem]">
        {typeof value === "string" ? <FinancialValue variant="compact">{value}</FinancialValue> : value}
      </div>
    </div>
  );
}

function AssetDrilldownButton({
  label,
  count,
  ariaLabel,
  onClick,
}: {
  label: string;
  count: number;
  ariaLabel: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "group flex min-h-[2.625rem] w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-border-interactive bg-surface-drilldown px-2.5 py-2 text-xs transition-colors",
        "hover:border-border-hover hover:bg-surface-drilldown-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-focus/45",
        "md:px-2 md:py-1.5"
      )}
      onClick={onClick}
      aria-label={ariaLabel}
    >
      <span className="text-foreground-secondary transition-colors group-hover:text-text-normal">{label}</span>
      <span className="font-mono text-sm font-medium tabular-nums text-accent transition-colors group-hover:text-primary">
        {count}
      </span>
    </button>
  );
}

export function AssetMetricsGrid({
  asset,
  onDrilldown,
  mobile = false,
}: {
  asset: AssetAnalyticsRow;
  onDrilldown: (mode: "open" | "closed") => void;
  mobile?: boolean;
}) {
  const cap = asset.global_risk_cap_pct ?? 2;

  return (
    <div
      className={cn(
        "grid grid-cols-2 text-xs leading-snug",
        mobile ? "gap-2.5" : "gap-x-2 gap-y-1.5"
      )}
    >
      <AssetDrilldownButton
        label={t("home.open_positions")}
        count={asset.open_positions}
        ariaLabel={t("home.open_positions_modal_title", { asset: asset.symbol })}
        onClick={() => onDrilldown("open")}
      />
      <AssetDrilldownButton
        label={t("home.closed_trades")}
        count={asset.closed_trades}
        ariaLabel={t("home.closed_trades_modal_title", { asset: asset.symbol })}
        onClick={() => onDrilldown("closed")}
      />
      <div className="min-h-[2.625rem] rounded-md bg-surface-inner px-2 py-1.5">
        <p className="text-muted">{t("home.realized_pnl")}</p>
        <div className="mt-0.5 min-h-[1rem]">
          <PnLDisplay value={asset.realized_pnl} size="sm" />
        </div>
      </div>
      <div className="min-h-[2.625rem] rounded-md bg-surface-inner px-2 py-1.5">
        <p className="text-muted">{t("home.unrealized_pnl")}</p>
        <div className="mt-0.5 min-h-[1rem]">
          <PnLDisplay value={asset.unrealized_pnl} size="sm" />
        </div>
      </div>
      <div className="col-span-2 flex min-h-[2.625rem] items-baseline justify-between gap-2 rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
        <span className="text-muted">{t("home.total_pnl")}</span>
        <PnLDisplay value={asset.total_pnl} size="sm" />
      </div>
      <MetricCell label={t("home.asset_exposure_short")} value={formatAssetExposure(asset)} />
      <MetricCell label={t("home.asset_risk_short")} value={formatAssetRisk(asset)} />
      <MetricCell label={t("home.asset_target_profit_short")} value={formatAssetTargetProfit(asset)} />
      <MetricCell label={t("home.asset_risk_reward_short")} value={formatAssetRiskReward(asset)} />
      <div
        className="col-span-2 min-h-[3.25rem] rounded-md border border-border-nested bg-surface-inner px-2 py-1.5"
        title={t("home.asset_risk_cap_hint", { cap: formatRiskPercent(cap) })}
      >
        <p className="text-muted">{t("home.asset_risk_pct_short")}</p>
        <div className="mt-0.5 space-y-0.5">
          <p className="flex items-baseline justify-between gap-2">
            <span className="text-muted">{t("home.asset_risk_current_pct")}</span>
            <FinancialValue variant="compact">{formatCurrentRiskPct(asset)}</FinancialValue>
          </p>
          <p className="flex items-baseline justify-between gap-2">
            <span className="text-muted">{t("home.asset_risk_limit_pct")}</span>
            <FinancialValue variant="compact">{formatRiskPercent(cap)}</FinancialValue>
          </p>
        </div>
      </div>
    </div>
  );
}
