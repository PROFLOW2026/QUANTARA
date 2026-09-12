"use client";

import { useState } from "react";
import { AssetChartModal } from "@/components/trading/AssetChartModal";
import { AssetTradeDrilldownModal } from "@/components/trading/AssetTradeDrilldownModal";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import {
  type AssetAnalyticsRow,
  type Decision,
  type MarketProviderStatus,
  type ProviderHealthStatus,
} from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatRiskPercentOrUnavailable,
  formatProviderUsageLine,
  resolveAssetDataStatusPresentation,
  translateProviderStatus,
  translateStructureRegime,
  translateVolatilityRegime,
} from "@/lib/display-text";
import { formatRiskRewardLabel, formatTargetProfitOrUnavailable } from "@/lib/profit-target";
import { t } from "@/lib/i18n";
import { cn, formatCurrency, formatRiskPercent, formatRelativeTime } from "@/lib/utils";

function statusBadge(
  status: string,
  stale?: boolean,
  sessionClosed?: boolean,
  hasLastCandle?: boolean
) {
  const { label, variant } = resolveAssetDataStatusPresentation(status, {
    stale,
    sessionClosed,
    hasLastCandle,
  });
  return <Badge variant={variant}>{label}</Badge>;
}

function providerLabel(name: string) {
  if (name === "twelvedata") return "Twelve Data";
  if (name === "tiingo") return "Tiingo";
  if (name === "alpaca") return "Alpaca";
  return name;
}

function formatAssetExposure(asset: AssetAnalyticsRow) {
  if (asset.open_exposure != null) {
    return formatCurrencyOrUnavailable(asset.open_exposure);
  }
  if ((asset.open_positions ?? 0) === 0) {
    return formatCurrencyOrUnavailable(0);
  }
  return formatCurrencyOrUnavailable(null);
}

function formatAssetRisk(asset: AssetAnalyticsRow) {
  if (asset.open_risk_usd != null) {
    return formatCurrencyOrUnavailable(asset.open_risk_usd);
  }
  if ((asset.open_positions ?? 0) === 0) {
    return formatCurrencyOrUnavailable(0);
  }
  return formatCurrencyOrUnavailable(null);
}

function formatAssetTargetProfit(asset: AssetAnalyticsRow) {
  if ((asset.open_positions ?? 0) === 0) {
    return formatCurrencyOrUnavailable(0);
  }
  if (asset.open_target_profit_usd != null) {
    return formatTargetProfitOrUnavailable(asset.open_target_profit_usd);
  }
  return formatTargetProfitOrUnavailable(null);
}

function formatAssetRiskReward(asset: AssetAnalyticsRow) {
  if ((asset.open_positions ?? 0) === 0) {
    return formatRiskRewardLabel(0);
  }
  return formatRiskRewardLabel(asset.combined_risk_reward);
}

function formatRiskPctBlock(asset: AssetAnalyticsRow) {
  const cap = asset.global_risk_cap_pct ?? 2;
  const currentPct =
    asset.open_risk_pct != null
      ? formatRiskPercentOrUnavailable(asset.open_risk_pct)
      : (asset.open_positions ?? 0) === 0
        ? formatRiskPercentOrUnavailable(0)
        : formatRiskPercentOrUnavailable(null);
  return (
    <div className="space-y-0.5" title={t("home.asset_risk_cap_hint", { cap: formatRiskPercent(cap) })}>
      <p>
        <span className="text-muted">{t("home.asset_risk_current_pct")}: </span>
        <span className="font-mono text-financial">{currentPct}</span>
      </p>
      <p>
        <span className="text-muted">{t("home.asset_risk_limit_pct")}: </span>
        <span className="font-mono text-financial">{formatRiskPercent(cap)}</span>
      </p>
    </div>
  );
}

function providerStatusBadge(status: string) {
  const variant =
    status === "healthy"
      ? "success"
      : status === "blocked" || status === "exhausted"
        ? "danger"
        : status === "conservation" || status === "error"
          ? "warning"
          : "muted";
  return <Badge variant={variant}>{translateProviderStatus(status)}</Badge>;
}

function ProviderHealthCard({
  name,
  health,
}: {
  name: string;
  health?: ProviderHealthStatus;
}) {
  const status = health?.status ?? "unknown";
  const usageLine = health ? formatProviderUsageLine(name, health) : null;
  return (
    <div className="rounded-md border border-border-nested bg-surface-inner p-3 text-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="font-medium">{providerLabel(name)}</p>
        {providerStatusBadge(status)}
      </div>
      {health?.last_success ? (
        <p className="text-xs text-muted">
          {t("home.provider_last_update")}: {formatRelativeTime(health.last_success)}
        </p>
      ) : null}
      {usageLine ? <p className="text-xs text-muted">{usageLine}</p> : null}
      {name === "twelvedata" && health?.guard_limit != null ? (
        <p className="text-xs text-muted">
          {t("home.provider_guard_limit", { limit: health.guard_limit })}
        </p>
      ) : null}
    </div>
  );
}

export function ActiveAssetsSummary({
  assetsActive,
  providers,
}: {
  assetsActive: number;
  providers: string[];
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader>
          <CardTitle>{t("home.assets_active")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{assetsActive}</p>
        </CardContent>
      </Card>
      <Card className="sm:col-span-2 lg:col-span-3">
        <CardHeader>
          <CardTitle>{t("home.providers_active")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {providers.map((p) => (
            <Badge key={p} variant="outline">
              {providerLabel(p)}
            </Badge>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function AssetSymbolButton({
  asset,
  onOpen,
  className = "",
}: {
  asset: AssetAnalyticsRow;
  onOpen: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`group inline-flex min-w-0 max-w-full cursor-pointer items-center gap-1 rounded-sm text-right transition-colors hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 ${className}`}
      onClick={onOpen}
      aria-label={t("home.asset_chart_open_aria", { asset: asset.symbol })}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 16 16"
        className="h-3.5 w-3.5 shrink-0 opacity-45 transition-opacity group-hover:opacity-90"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M2 12 6 7.5 9 10 14 4" />
        <path d="M11 4h3v3" />
      </svg>
      <span className="truncate font-semibold leading-tight group-hover:underline">{asset.symbol}</span>
    </button>
  );
}

function AssetDrilldownButton({
  label,
  count,
  ariaLabel,
  onClick,
  className = "",
}: {
  label: string;
  count: number;
  ariaLabel: string;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={cn(
        "group flex w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-border-interactive bg-surface-drilldown px-2.5 py-2 text-xs transition-colors",
        "hover:border-border-hover hover:bg-surface-drilldown-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-focus/45",
        "md:px-2 md:py-1.5",
        className
      )}
      onClick={onClick}
      aria-label={ariaLabel}
    >
      <span className="text-foreground-secondary transition-colors group-hover:text-text-normal">{label}</span>
      <span className="font-mono text-sm font-medium text-accent transition-colors group-hover:text-primary">
        {count}
      </span>
    </button>
  );
}

function DesktopActiveAssetCard({
  asset,
  onDrilldown,
  onChartOpen,
}: {
  asset: AssetAnalyticsRow;
  onDrilldown: (mode: "open" | "closed") => void;
  onChartOpen: () => void;
}) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border border-border bg-surface text-sm shadow-card">
      <div className="border-b border-border-nested bg-surface-header p-3">
        <div className="flex items-start justify-between gap-2">
          <AssetSymbolButton asset={asset} onOpen={onChartOpen} className="text-base text-foreground" />
          <p className="shrink-0 font-mono text-base leading-tight text-financial">
            {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
          </p>
        </div>
        <p className="mt-0.5 h-4 truncate text-xs leading-4 text-text-subtle">
          {providerLabel(asset.provider)} · {t(`home.session_${asset.session_status}`)}
        </p>
        <div className="mt-1 flex h-5 items-center">
          {statusBadge(
            asset.data_status,
            asset.stale,
            asset.session_closed,
            Boolean(asset.last_candle)
          )}
        </div>
      </div>

      <div className="flex flex-1 flex-col p-3">
      <div className="mb-2 border-b border-border-nested pb-2 text-xs leading-snug">
        <span className="text-muted">{t("home.market_regime_title")}: </span>
        {asset.market_regime ? (
          <>
            <span className="text-text-normal">{translateStructureRegime(asset.market_regime.structure_regime)}</span>
            <span className="text-muted">
              {" "}
              · {translateVolatilityRegime(asset.market_regime.volatility_regime)}
            </span>
          </>
        ) : (
          <span>—</span>
        )}
      </div>

      <div className="mt-auto grid grid-cols-2 gap-x-2 gap-y-1.5 text-xs leading-snug">
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
        <div className="rounded-md bg-surface-inner px-2 py-1.5">
          <p className="text-muted">{t("home.realized_pnl")}</p>
          <div className="mt-0.5">
            <PnLDisplay value={asset.realized_pnl} size="sm" />
          </div>
        </div>
        <div className="rounded-md bg-surface-inner px-2 py-1.5">
          <p className="text-muted">{t("home.unrealized_pnl")}</p>
          <div className="mt-0.5">
            <PnLDisplay value={asset.unrealized_pnl} size="sm" />
          </div>
        </div>
        <div className="col-span-2 flex items-baseline justify-between gap-2 rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
          <span className="text-muted">{t("home.total_pnl")}</span>
          <PnLDisplay value={asset.total_pnl} size="sm" />
        </div>
        {(asset.open_positions ?? 0) > 0 ? (
          <>
            <div className="col-span-2 grid grid-cols-2 gap-x-2 gap-y-1.5">
              <div className="rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
                <p className="text-muted">{t("home.asset_exposure_short")}</p>
                <p className="mt-0.5 font-mono text-financial">{formatAssetExposure(asset)}</p>
              </div>
              <div className="rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
                <p className="text-muted">{t("home.asset_risk_short")}</p>
                <p className="mt-0.5 font-mono text-financial">{formatAssetRisk(asset)}</p>
              </div>
              <div className="rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
                <p className="text-muted">{t("home.asset_target_profit_short")}</p>
                <p className="mt-0.5 font-mono text-financial">{formatAssetTargetProfit(asset)}</p>
              </div>
              <div className="rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
                <p className="text-muted">{t("home.asset_risk_reward_short")}</p>
                <p className="mt-0.5 font-mono text-financial">{formatAssetRiskReward(asset)}</p>
              </div>
            </div>
            <div className="col-span-2 rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
              <p className="text-muted">{t("home.asset_risk_pct_short")}</p>
              <div className="mt-0.5 text-xs">{formatRiskPctBlock(asset)}</div>
            </div>
          </>
        ) : null}
      </div>
      </div>
    </div>
  );
}

export function ActiveAssetsTable({
  assets,
  assetDecisions = [],
}: {
  assets: AssetAnalyticsRow[];
  assetDecisions?: Decision[];
}) {
  const [drilldown, setDrilldown] = useState<{
    asset: AssetAnalyticsRow;
    mode: "open" | "closed";
  } | null>(null);
  const [chartAsset, setChartAsset] = useState<AssetAnalyticsRow | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.active_assets_title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3 md:hidden">
          {assets.map((asset) => (
            <div
              key={`mobile-${asset.db_symbol}`}
              className="overflow-hidden rounded-md border border-border bg-surface text-sm shadow-card"
            >
              <div className="border-b border-border-nested bg-surface-header px-4 py-3.5">
                <AssetSymbolButton asset={asset} onOpen={() => setChartAsset(asset)} className="font-medium" />
                <p className="mt-1.5 text-xs text-text-subtle">
                  {providerLabel(asset.provider)} · {t(`home.session_${asset.session_status}`)}
                </p>
                <p className="mt-1.5 font-mono text-financial">
                  {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
                </p>
                <div className="mt-2">
                  {statusBadge(
                    asset.data_status,
                    asset.stale,
                    asset.session_closed,
                    Boolean(asset.last_candle)
                  )}
                </div>
              </div>
              <div className="px-4 py-4">
                <div className="grid grid-cols-2 gap-2.5 text-xs">
                  <AssetDrilldownButton
                    label={t("home.open_positions")}
                    count={asset.open_positions}
                    ariaLabel={t("home.open_positions_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "open" })}
                  />
                  <AssetDrilldownButton
                    label={t("home.closed_trades")}
                    count={asset.closed_trades}
                    ariaLabel={t("home.closed_trades_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "closed" })}
                  />
                  <p>
                    {t("home.realized_pnl")}: <PnLDisplay value={asset.realized_pnl} size="sm" />
                  </p>
                  <p>
                    {t("home.unrealized_pnl")}: <PnLDisplay value={asset.unrealized_pnl} size="sm" />
                  </p>
                  <p className="col-span-2">
                    {t("home.total_pnl")}: <PnLDisplay value={asset.total_pnl} size="sm" />
                  </p>
                  {(asset.open_positions ?? 0) > 0 ? (
                    <>
                      <p>
                        {t("home.asset_exposure_short")}: {formatAssetExposure(asset)}
                      </p>
                      <p>
                        {t("home.asset_risk_short")}: {formatAssetRisk(asset)}
                      </p>
                      <p>
                        {t("home.asset_target_profit_short")}: {formatAssetTargetProfit(asset)}
                      </p>
                      <p>
                        {t("home.asset_risk_reward_short")}: {formatAssetRiskReward(asset)}
                      </p>
                      <div className="col-span-2">{formatRiskPctBlock(asset)}</div>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
        </div>
        <div className="hidden gap-3 md:grid md:grid-cols-2 lg:grid-cols-4">
          {assets.map((asset) => (
            <DesktopActiveAssetCard
              key={asset.db_symbol}
              asset={asset}
              onDrilldown={(mode) => setDrilldown({ asset, mode })}
              onChartOpen={() => setChartAsset(asset)}
            />
          ))}
        </div>
      </CardContent>
      <AssetChartModal
        open={chartAsset != null}
        asset={chartAsset}
        decisions={assetDecisions}
        onClose={() => setChartAsset(null)}
      />
      <AssetTradeDrilldownModal
        open={drilldown != null}
        mode={drilldown?.mode ?? "open"}
        asset={drilldown?.asset ?? null}
        onClose={() => setDrilldown(null)}
      />
    </Card>
  );
}

export function ProviderHealthPanel({
  marketStatus,
}: {
  marketStatus: MarketProviderStatus | null;
}) {
  const providers = marketStatus?.providers ?? {};
  const order = ["twelvedata", "tiingo", "alpaca"];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("home.provider_health_title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-3">
        {order.map((name) => (
          <ProviderHealthCard key={name} name={name} health={providers[name]} />
        ))}
      </CardContent>
    </Card>
  );
}
