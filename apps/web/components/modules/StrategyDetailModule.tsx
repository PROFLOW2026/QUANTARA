"use client";

import { ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { ModalLink } from "@/components/layout/ModalLink";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { t } from "@/lib/i18n";

const ROBOT_LABELS: Record<string, string> = {
  "gold-trend-pullback": "Robot A",
  "opening-range-breakout": "Robot B",
};

export default function StrategyDetailModule({ embedded, params = {} }: ModuleProps) {
  const slug = params.slug ?? "";
  const { data, error, loading } = useModuleData(async () => {
    const strategies = await api.getStrategies();
    let orbStatus = null;
    if (slug === "opening-range-breakout") {
      orbStatus = await api.getOrbStatus();
    }
    const strategy = strategies?.find((s) => s.slug === slug) ?? null;
    return { strategy, orbStatus };
  }, [slug]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const strategy = data?.strategy;
  const orbStatus = data?.orbStatus;

  return (
    <>
      {!embedded ? (
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">{strategy?.name ?? slug}</h1>
          {ROBOT_LABELS[slug] ? (
            <p className="mt-1 text-sm text-muted">{ROBOT_LABELS[slug]}</p>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      {strategy ? (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>{t("strategies.overview")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <span className="text-muted">{t("strategies.slug")}: </span>
              <span className="font-mono">{strategy.slug}</span>
            </p>
            {ROBOT_LABELS[slug] ? (
              <p>
                <span className="text-muted">{t("strategies.robot")}: </span>
                {ROBOT_LABELS[slug]}
              </p>
            ) : null}
            <p>
              <span className="text-muted">{t("common.status")}: </span>
              <StatusBadge status={strategy.status} />
            </p>
            <p>
              <span className="text-muted">{t("strategies.instruments")}: </span>
              {strategy.instruments?.join(", ") ?? "—"}
            </p>
            <ModalLink
              href={`/strategies/${slug}/versions`}
              className="inline-block text-accent hover:underline"
            >
              {t("strategies.view_versions")}
            </ModalLink>
          </CardContent>
        </Card>
      ) : null}

      {slug === "opening-range-breakout" ? (
        <p className="mb-4 text-sm text-muted">{t("backtests.orb_backtest_note")}</p>
      ) : null}

      {slug === "opening-range-breakout" && orbStatus ? (
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
      ) : null}
    </>
  );
}
