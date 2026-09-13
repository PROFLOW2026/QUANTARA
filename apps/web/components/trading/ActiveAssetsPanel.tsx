"use client";

import { useState } from "react";
import { AssetChartModal } from "@/components/trading/AssetChartModal";
import { AssetMetricsGrid } from "@/components/trading/AssetMetricsGrid";
import { AssetTradeDrilldownModal } from "@/components/trading/AssetTradeDrilldownModal";
import { AssetLivePrice } from "@/components/trading/AssetLivePrice";
import { FinancialValue } from "@/components/trading/FinancialValue";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  type AssetAnalyticsRow,
  type Decision,
  type MarketProviderStatus,
  type ProviderHealthStatus,
} from "@/lib/api-client";
import {
  formatProviderUsageLine,
  resolveAssetDataStatusPresentation,
  translateProviderStatus,
  translateStructureRegime,
  translateVolatilityRegime,
} from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { AssetTradingPauseControl } from "@/components/trading/AssetTradingControl";
import { formatCurrency, formatRelativeTime } from "@/lib/utils";

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

function DesktopActiveAssetCard({
  asset,
  onDrilldown,
  onChartOpen,
  onRefresh,
}: {
  asset: AssetAnalyticsRow;
  onDrilldown: (mode: "open" | "closed") => void;
  onChartOpen: () => void;
  onRefresh?: () => void;
}) {
  return (
    <div className="flex h-full min-h-[21.5rem] flex-col overflow-hidden rounded-md border border-border bg-surface text-sm shadow-card">
      <div className="shrink-0 border-b border-border-nested bg-surface-header p-3">
        <div className="flex items-start justify-between gap-2">
          <AssetSymbolButton asset={asset} onOpen={onChartOpen} className="text-base text-foreground" />
          <AssetLivePrice
            dbSymbol={asset.db_symbol}
            price={asset.latest_price}
            className="shrink-0 text-base"
          />
        </div>
        <p className="mt-0.5 h-4 truncate text-xs leading-4 text-text-subtle">
          {providerLabel(asset.provider)} · {t(`home.session_${asset.session_status}`)}
        </p>
        <div className="mt-1 flex min-h-5 flex-wrap items-center gap-2">
          {statusBadge(
            asset.data_status,
            asset.stale,
            asset.session_closed,
            Boolean(asset.last_candle)
          )}
          <AssetTradingPauseControl
            dbSymbol={asset.db_symbol}
            displaySymbol={asset.symbol}
            paused={asset.trading_paused}
            onChanged={onRefresh}
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="mb-2 min-h-[2.5rem] shrink-0 border-b border-border-nested pb-2 text-xs leading-snug">
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

        <div className="mt-auto min-h-0">
          <AssetMetricsGrid asset={asset} onDrilldown={onDrilldown} />
        </div>
      </div>
    </div>
  );
}

export function ActiveAssetsTable({
  assets,
  assetDecisions = [],
  onRefresh,
}: {
  assets: AssetAnalyticsRow[];
  assetDecisions?: Decision[];
  onRefresh?: () => void;
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
                <AssetLivePrice
                  dbSymbol={asset.db_symbol}
                  price={asset.latest_price}
                  className="mt-1.5"
                />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {statusBadge(
                    asset.data_status,
                    asset.stale,
                    asset.session_closed,
                    Boolean(asset.last_candle)
                  )}
                  <AssetTradingPauseControl
                    dbSymbol={asset.db_symbol}
                    displaySymbol={asset.symbol}
                    paused={asset.trading_paused}
                    onChanged={onRefresh}
                  />
                </div>
              </div>
              <div className="px-4 py-4">
                <div className="mb-3 min-h-[2.5rem] border-b border-border-nested pb-2 text-xs leading-snug">
                  <span className="text-muted">{t("home.market_regime_title")}: </span>
                  {asset.market_regime ? (
                    <>
                      <span className="text-text-normal">
                        {translateStructureRegime(asset.market_regime.structure_regime)}
                      </span>
                      <span className="text-muted">
                        {" "}
                        · {translateVolatilityRegime(asset.market_regime.volatility_regime)}
                      </span>
                    </>
                  ) : (
                    <span>—</span>
                  )}
                </div>
                <AssetMetricsGrid
                  asset={asset}
                  onDrilldown={(mode) => setDrilldown({ asset, mode })}
                  mobile
                />
              </div>
            </div>
          ))}
        </div>
        <div className="hidden gap-3 md:grid md:auto-rows-fr md:grid-cols-2 lg:grid-cols-4">
          {assets.map((asset) => (
            <DesktopActiveAssetCard
              key={asset.db_symbol}
              asset={asset}
              onDrilldown={(mode) => setDrilldown({ asset, mode })}
              onChartOpen={() => setChartAsset(asset)}
              onRefresh={onRefresh}
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
