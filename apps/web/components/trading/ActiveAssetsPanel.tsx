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

function CountDrilldownLink({
  count,
  label,
  onClick,
  className = "",
}: {
  count: number;
  label: string;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`cursor-pointer font-inherit text-accent hover:text-blue-400 hover:underline focus:outline-none focus-visible:underline focus-visible:ring-1 focus-visible:ring-accent/50 ${className}`}
      onClick={onClick}
      aria-label={label}
    >
      {count}
    </button>
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
                <p>
                  {t("home.open_positions")}:{" "}
                  <CountDrilldownLink
                    count={asset.open_positions}
                    label={t("home.open_positions_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "open" })}
                  />
                </p>
                <p>
                  {t("home.closed_trades")}:{" "}
                  <CountDrilldownLink
                    count={asset.closed_trades}
                    label={t("home.closed_trades_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "closed" })}
                  />
                </p>
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
        <table className="hidden w-full table-fixed text-xs md:table">
          <colgroup>
            <col className="w-[8%]" />
            <col className="w-[8%]" />
            <col className="w-[8%]" />
            <col className="w-[9%]" />
            <col className="w-[7%]" />
            <col className="w-[6%]" />
            <col className="w-[6%]" />
            <col className="w-[9%]" />
            <col className="w-[9%]" />
            <col className="w-[9%]" />
            <col className="w-[9%]" />
            <col className="w-[6%]" />
            <col className="w-[6%]" />
          </colgroup>
          <thead>
            <tr className="border-b border-border text-muted">
              <th className="py-2 pe-1 text-right">{t("home.asset_symbol")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_provider")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_price")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_freshness")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_session")}</th>
              <th className="px-1 py-2 text-right">{t("home.market_regime_title")}</th>
              <th className="px-1 py-2 text-right">{t("home.open_positions")}</th>
              <th className="px-1 py-2 text-right">{t("home.closed_trades")}</th>
              <th className="px-1 py-2 text-right">{t("home.realized_pnl")}</th>
              <th className="px-1 py-2 text-right">{t("home.unrealized_pnl")}</th>
              <th className="px-1 py-2 text-right">{t("home.total_pnl")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_exposure_short")}</th>
              <th className="px-1 py-2 text-right">{t("home.asset_risk_short")}</th>
              <th className="ps-1 py-2 text-right">{t("home.asset_risk_pct_short")}</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <tr key={asset.db_symbol} className="border-b border-border/50">
                <td className="truncate py-2 pe-1 font-medium">{asset.symbol}</td>
                <td className="truncate px-1 py-2">{providerLabel(asset.provider)}</td>
                <td className="truncate px-1 py-2 font-mono">
                  {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
                </td>
                <td className="px-1 py-2">
                  {statusBadge(asset.data_status, asset.stale, asset.session_closed)}
                </td>
                <td className="truncate px-1 py-2">
                  {t(`home.session_${asset.session_status}`)}
                </td>
                <td className="px-1 py-2 text-xs">
                  {asset.market_regime ? (
                    <div>
                      <div>{translateStructureRegime(asset.market_regime.structure_regime)}</div>
                      <div className="text-muted">
                        {translateVolatilityRegime(asset.market_regime.volatility_regime)}
                      </div>
                    </div>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-1 py-2 text-right">
                  <CountDrilldownLink
                    count={asset.open_positions}
                    label={t("home.open_positions_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "open" })}
                    className="font-mono"
                  />
                </td>
                <td className="px-1 py-2 text-right">
                  <CountDrilldownLink
                    count={asset.closed_trades}
                    label={t("home.closed_trades_modal_title", { asset: asset.symbol })}
                    onClick={() => setDrilldown({ asset, mode: "closed" })}
                    className="font-mono"
                  />
                </td>
                <td className="px-1 py-2 text-right">
                  <PnLDisplay value={asset.realized_pnl} size="sm" />
                </td>
                <td className="px-1 py-2 text-right">
                  <PnLDisplay value={asset.unrealized_pnl} size="sm" />
                </td>
                <td className="px-1 py-2 text-right">
                  <PnLDisplay value={asset.total_pnl} size="sm" />
                </td>
                <td className="truncate px-1 py-2 text-right font-mono">
                  {formatAssetExposure(asset)}
                </td>
                <td className="truncate px-1 py-2 text-right font-mono">
                  {formatAssetRisk(asset)}
                </td>
                <td className="truncate ps-1 py-2 text-right font-mono">{formatRiskPct(asset)}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
