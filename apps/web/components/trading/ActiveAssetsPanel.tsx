"use client";

import { useState } from "react";
import { AssetTradeDrilldownModal } from "@/components/trading/AssetTradeDrilldownModal";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import {
  type AssetAnalyticsRow,
  type MarketProviderStatus,
  type ProviderHealthStatus,
} from "@/lib/api-client";
import {
  formatCurrencyOrUnavailable,
  formatPercentOrUnavailable,
  formatProviderUsageLine,
  providerHasTechnicalDetails,
  translateDataStatus,
  translateProviderStatus,
  translateStructureRegime,
  translateVolatilityRegime,
} from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent, formatRelativeTime } from "@/lib/utils";

function statusBadge(status: string, stale?: boolean, sessionClosed?: boolean) {
  const effectiveStatus =
    sessionClosed && status !== "error" && status !== "blocked" ? "deferred" : status;
  const key = stale && !sessionClosed ? "stale" : effectiveStatus;
  const variant =
    key === "healthy" || key === "fresh"
      ? "success"
      : key === "deferred"
        ? "muted"
        : key === "blocked" || key === "error" || key === "exhausted"
          ? "danger"
          : key === "conservation"
            ? "warning"
            : "warning";
  return (
    <Badge variant={variant}>
      {translateDataStatus(effectiveStatus, stale && !sessionClosed, sessionClosed)}
    </Badge>
  );
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

function formatRiskPct(asset: AssetAnalyticsRow) {
  const cap = asset.global_risk_cap_pct ?? 2;
  const pctLabel =
    asset.open_risk_pct != null
      ? formatPercentOrUnavailable(asset.open_risk_pct)
      : (asset.open_positions ?? 0) === 0
        ? formatPercentOrUnavailable(0)
        : formatPercentOrUnavailable(null);
  return (
    <span title={t("home.asset_risk_cap_hint", { cap: formatPercent(cap) })}>
      {pctLabel} / {formatPercent(cap)}
    </span>
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
  const [showDetails, setShowDetails] = useState(false);
  const status = health?.status ?? "unknown";
  const usageLine = health ? formatProviderUsageLine(name, health) : null;
  const hasTechnicalDetails = providerHasTechnicalDetails(health);
  return (
    <div className="rounded-md bg-surface-elevated p-3 text-sm">
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
      {name === "tiingo" && health?.fallback_mode ? (
        <p className="text-xs text-muted">{t("home.provider_fallback_active")}</p>
      ) : null}
      {hasTechnicalDetails ? (
        <div className="mt-2">
          <button
            type="button"
            className="text-xs text-accent underline-offset-2 hover:underline"
            onClick={() => setShowDetails((open) => !open)}
            aria-expanded={showDetails}
          >
            {showDetails ? t("home.provider_details_hide") : t("home.provider_details")}
          </button>
          {showDetails ? (
            <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded border border-border/60 bg-background/50 p-2 text-[10px] leading-snug text-muted">
              {t("home.provider_details_technical")}:{"\n"}
              {health?.last_error?.trim()}
            </pre>
          ) : null}
        </div>
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
      className={`group flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg border border-border/90 bg-background/75 px-2 py-1.5 text-xs shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05),0_1px_2px_rgba(0,0,0,0.14)] transition-[color,background-color,border-color,box-shadow,transform] duration-150 hover:border-accent/30 hover:bg-background hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_2px_6px_rgba(0,0,0,0.18)] active:translate-y-px active:border-accent/20 active:bg-background/60 active:shadow-[inset_0_1px_2px_rgba(0,0,0,0.18)] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 focus-visible:ring-offset-1 focus-visible:ring-offset-background ${className}`}
      onClick={onClick}
      aria-label={ariaLabel}
    >
      <span className="text-muted transition-colors group-hover:text-foreground/85">{label}</span>
      <span className="font-mono text-sm text-accent transition-colors group-hover:text-blue-300">
        {count}
      </span>
    </button>
  );
}

function DesktopActiveAssetCard({
  asset,
  onDrilldown,
}: {
  asset: AssetAnalyticsRow;
  onDrilldown: (mode: "open" | "closed") => void;
}) {
  return (
    <div className="flex h-full flex-col rounded-md border border-border/60 bg-surface-elevated/30 p-3 text-sm">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-base font-semibold leading-tight">{asset.symbol}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="text-xs text-muted leading-snug">
              {providerLabel(asset.provider)} · {t(`home.session_${asset.session_status}`)}
            </p>
            {statusBadge(asset.data_status, asset.stale, asset.session_closed)}
          </div>
        </div>
        <p className="shrink-0 font-mono text-base leading-tight">
          {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
        </p>
      </div>

      <div className="mb-2 text-xs leading-snug">
        <span className="text-muted">{t("home.market_regime_title")}: </span>
        {asset.market_regime ? (
          <>
            <span>{translateStructureRegime(asset.market_regime.structure_regime)}</span>
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
        <div>
          <p className="text-muted">{t("home.realized_pnl")}</p>
          <div className="mt-0.5">
            <PnLDisplay value={asset.realized_pnl} size="sm" />
          </div>
        </div>
        <div>
          <p className="text-muted">{t("home.unrealized_pnl")}</p>
          <div className="mt-0.5">
            <PnLDisplay value={asset.unrealized_pnl} size="sm" />
          </div>
        </div>
        <div className="col-span-2 flex items-baseline justify-between gap-2 border-t border-border/40 pt-1.5">
          <span className="text-muted">{t("home.total_pnl")}</span>
          <PnLDisplay value={asset.total_pnl} size="sm" />
        </div>
        <div className="col-span-2 grid grid-cols-3 gap-x-2">
          <div>
            <p className="text-muted">{t("home.asset_exposure_short")}</p>
            <p className="mt-0.5 font-mono">{formatAssetExposure(asset)}</p>
          </div>
          <div>
            <p className="text-muted">{t("home.asset_risk_short")}</p>
            <p className="mt-0.5 font-mono">{formatAssetRisk(asset)}</p>
          </div>
          <div>
            <p className="text-muted">{t("home.asset_risk_pct_short")}</p>
            <p className="mt-0.5 font-mono">{formatRiskPct(asset)}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ActiveAssetsTable({ assets }: { assets: AssetAnalyticsRow[] }) {
  const [drilldown, setDrilldown] = useState<{
    asset: AssetAnalyticsRow;
    mode: "open" | "closed";
  } | null>(null);

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
              className="rounded-md border border-border/60 p-3 text-sm"
            >
              <p className="font-medium">{asset.symbol}</p>
              <p className="mt-1 text-xs text-muted">
                {providerLabel(asset.provider)} · {t(`home.session_${asset.session_status}`)}
              </p>
              <p className="mt-1 font-mono">
                {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
              </p>
              <div className="mt-2">{statusBadge(asset.data_status, asset.stale)}</div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
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
                <p>
                  {t("home.asset_exposure_short")}: {formatAssetExposure(asset)}
                </p>
                <p>
                  {t("home.asset_risk_short")}: {formatAssetRisk(asset)}
                </p>
                <p className="col-span-2">
                  {t("home.asset_risk_pct_short")}: {formatRiskPct(asset)}
                </p>
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
            />
          ))}
        </div>
      </CardContent>
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
