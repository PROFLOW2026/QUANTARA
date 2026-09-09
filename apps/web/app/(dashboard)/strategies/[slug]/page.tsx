import Link from "next/link";
import { notFound } from "next/navigation";
import { ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";

const ROBOT_LABELS: Record<string, string> = {
  "gold-trend-pullback": "Robot A",
  "opening-range-breakout": "Robot B",
};

export default async function StrategyDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  let strategies = null;
  let orbStatus = null;
  let error: string | null = null;

  try {
    strategies = await api.getStrategies();
    if (slug === "opening-range-breakout") {
      orbStatus = await api.getOrbStatus();
    }
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  const strategy = strategies?.find((s) => s.slug === slug);
  if (!strategy && !error) {
    notFound();
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-100">{strategy?.name ?? slug}</h1>
        {ROBOT_LABELS[slug] && (
          <p className="mt-1 text-sm text-muted">{ROBOT_LABELS[slug]}</p>
        )}
      </div>
      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} />
        </div>
      )}

      {strategy && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>{t("strategies.overview")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <span className="text-muted">{t("strategies.slug")}: </span>
              <span className="font-mono">{strategy.slug}</span>
            </p>
            {ROBOT_LABELS[slug] && (
              <p>
                <span className="text-muted">{t("strategies.robot")}: </span>
                {ROBOT_LABELS[slug]}
              </p>
            )}
            <p>
              <span className="text-muted">{t("common.status")}: </span>
              <StatusBadge status={strategy.status} />
            </p>
            <p>
              <span className="text-muted">{t("strategies.instruments")}: </span>
              {strategy.instruments?.join(", ") ?? "—"}
            </p>
            <Link
              href={`/strategies/${slug}/versions`}
              className="inline-block text-accent hover:underline"
            >
              {t("strategies.view_versions")}
            </Link>
          </CardContent>
        </Card>
      )}

      {slug === "opening-range-breakout" && (
        <p className="mb-4 text-sm text-muted">{t("backtests.orb_backtest_note")}</p>
      )}

      {slug === "opening-range-breakout" && orbStatus && (
        <>
          <Card className="mb-6">
            <CardHeader>
              <CardTitle>{t("strategies.orb_status")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>
                <span className="text-muted">{t("strategies.paper_enabled")}: </span>
                {orbStatus.paper_enabled ? t("common.yes") : t("common.no")}
              </p>
              <p>
                <span className="text-muted">{t("strategies.portfolios")}: </span>
                {orbStatus.portfolios_count}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{t("strategies.orb_assets")}</CardTitle>
            </CardHeader>
            <CardContent>
              {!Object.keys(orbStatus.assets).length ? (
                <EmptyState message={t("strategies.empty")} />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("common.symbol")}</TableHead>
                      <TableHead>{t("strategies.range_high")}</TableHead>
                      <TableHead>{t("strategies.range_low")}</TableHead>
                      <TableHead>{t("strategies.range_size")}</TableHead>
                      <TableHead>{t("strategies.relation")}</TableHead>
                      <TableHead>{t("strategies.latest_signal")}</TableHead>
                      <TableHead>{t("strategies.trades_today")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(orbStatus.assets).map(([symbol, asset]) => (
                      <TableRow key={symbol}>
                        <TableCell className="font-medium">{symbol}</TableCell>
                        <TableCell>{asset.opening_range_high?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell>{asset.opening_range_low?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell>{asset.opening_range_size?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell>{asset.current_relation ?? "—"}</TableCell>
                        <TableCell>{asset.latest_reason ?? asset.latest_signal ?? "—"}</TableCell>
                        <TableCell>{asset.trades_today}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
