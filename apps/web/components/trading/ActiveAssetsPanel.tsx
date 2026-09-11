"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import {
  type AssetAnalyticsRow,
  type MarketProviderStatus,
  type ProviderHealthStatus,
} from "@/lib/api-client";
import { translateDataStatus } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatCurrency, formatRelativeTime } from "@/lib/utils";

function statusBadge(status: string, stale?: boolean) {
  const key = stale ? "stale" : status;
  const variant =
    key === "healthy" || key === "fresh"
      ? "success"
      : key === "deferred"
        ? "muted"
      : key === "blocked" || key === "error"
        ? "danger"
        : "warning";
  return <Badge variant={variant}>{translateDataStatus(status, stale)}</Badge>;
}

function tfBadge(available: boolean) {
  return (
    <span className={available ? "text-success" : "text-muted"}>
      {available ? "✓" : "—"}
    </span>
  );
}

function providerLabel(name: string) {
  if (name === "twelvedata") return "Twelve Data";
  if (name === "tiingo") return "Tiingo";
  if (name === "alpaca") return "Alpaca";
  return name;
}

function ProviderHealthCard({
  name,
  health,
}: {
  name: string;
  health?: ProviderHealthStatus;
}) {
  const status = health?.status ?? "unknown";

  return (
    <div className="rounded-md bg-surface-elevated p-3 text-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="font-medium">{providerLabel(name)}</p>
        {statusBadge(status)}
      </div>
      {health && health.last_success ? (
        <p className="text-xs text-muted">
          {t("home.provider_last_update")}: {formatRelativeTime(health.last_success)}
        </p>
      ) : null}
      {health && health.remaining != null ? (
        <p className="text-xs text-muted">
          {t("home.provider_credits")}: {health.used_today ?? 0}/
          {health.guard_limit ?? health.daily_limit ?? "—"}
        </p>
      ) : null}
      {health && health.remaining_hour != null ? (
        <p className="text-xs text-muted">
          {t("home.provider_hourly")}: {health.used_hour ?? 0}/{health.hourly_limit ?? "—"}
        </p>
      ) : null}
      {health && health.last_error ? (
        <p className="mt-1 text-xs text-warning truncate" title={health.last_error}>
          {health.last_error}
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

export function ActiveAssetsTable({ assets }: { assets: AssetAnalyticsRow[] }) {
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
              <div className="mt-2 flex gap-4 text-xs">
                <span>5m {tfBadge(asset.timeframes_available["5m"])}</span>
                <span>15m {tfBadge(asset.timeframes_available["15m"])}</span>
                <span>1h {tfBadge(asset.timeframes_available["1h"])}</span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <p>{t("home.open_positions")}: {asset.open_positions}</p>
                <p>{t("home.closed_trades")}: {asset.closed_trades}</p>
                <p>{t("home.realized_pnl")}: <PnLDisplay value={asset.realized_pnl} size="sm" /></p>
                <p>{t("home.unrealized_pnl")}: <PnLDisplay value={asset.unrealized_pnl} size="sm" /></p>
                <p className="col-span-2">{t("home.total_pnl")}: <PnLDisplay value={asset.total_pnl} size="sm" /></p>
              </div>
            </div>
          ))}
        </div>
        <table className="hidden w-full table-fixed text-sm md:table">
          <colgroup>
            <col className="w-[7%]" />
            <col className="w-[8%]" />
            <col className="w-[8%]" />
            <col className="w-[9%]" />
            <col className="w-[7%]" />
            <col className="w-[4%]" />
            <col className="w-[4%]" />
            <col className="w-[4%]" />
            <col className="w-[7%]" />
            <col className="w-[7%]" />
            <col className="w-[11%]" />
            <col className="w-[11%]" />
            <col className="w-[11%]" />
          </colgroup>
          <thead>
            <tr className="border-b border-border text-muted">
              <th className="py-2 pe-2 text-right">{t("home.asset_symbol")}</th>
              <th className="py-2 px-1 text-right">{t("home.asset_provider")}</th>
              <th className="py-2 px-1 text-right">{t("home.asset_price")}</th>
              <th className="py-2 px-1 text-right">{t("home.asset_freshness")}</th>
              <th className="py-2 px-1 text-right">{t("home.asset_session")}</th>
              <th className="py-2 px-1 text-center">5m</th>
              <th className="py-2 px-1 text-center">15m</th>
              <th className="py-2 px-1 text-center">1h</th>
              <th className="py-2 ps-3 pe-2 text-right">{t("home.open_positions")}</th>
              <th className="py-2 px-2 text-right">{t("home.closed_trades")}</th>
              <th className="py-2 px-2 text-right">{t("home.realized_pnl")}</th>
              <th className="py-2 px-2 text-right">{t("home.unrealized_pnl")}</th>
              <th className="py-2 ps-2 text-right">{t("home.total_pnl")}</th>
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <tr key={asset.db_symbol} className="border-b border-border/50">
                <td className="py-2 pe-2 font-medium truncate">{asset.symbol}</td>
                <td className="py-2 px-1 truncate">{providerLabel(asset.provider)}</td>
                <td className="py-2 px-1 font-mono truncate">
                  {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
                </td>
                <td className="py-2 px-1">{statusBadge(asset.data_status, asset.stale)}</td>
                <td className="py-2 px-1 truncate">{t(`home.session_${asset.session_status}`)}</td>
                <td className="py-2 px-1 text-center">
                  {tfBadge(asset.timeframes_available["5m"])}
                </td>
                <td className="py-2 px-1 text-center">
                  {tfBadge(asset.timeframes_available["15m"])}
                </td>
                <td className="py-2 px-1 text-center">
                  {tfBadge(asset.timeframes_available["1h"])}
                </td>
                <td className="py-2 ps-3 pe-2 text-right">{asset.open_positions}</td>
                <td className="py-2 px-2 text-right">{asset.closed_trades}</td>
                <td className="py-2 px-2 text-right">
                  <PnLDisplay value={asset.realized_pnl} size="sm" />
                </td>
                <td className="py-2 px-2 text-right">
                  <PnLDisplay value={asset.unrealized_pnl} size="sm" />
                </td>
                <td className="py-2 ps-2 text-right">
                  <PnLDisplay value={asset.total_pnl} size="sm" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
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
