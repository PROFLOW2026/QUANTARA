"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RiskConcentrationResponse } from "@/lib/api-client";
import { formatCurrencyOrUnavailable } from "@/lib/display-text";
import { t } from "@/lib/i18n";

export function RiskConcentrationPanel({
  data,
  loading,
}: {
  data: RiskConcentrationResponse | null | undefined;
  loading?: boolean;
}) {
  return (
    <section className="mt-6">
      <div className="mb-3">
        <h2 className="text-base font-semibold">{t("home.risk_concentration_title")}</h2>
        <p className="text-xs text-muted">{t("home.risk_concentration_hint")}</p>
      </div>
      {loading ? (
        <p className="text-sm text-muted">{t("common.loading")}</p>
      ) : !data ? (
        <p className="text-sm text-muted">{t("common.section_unavailable")}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>{t("home.risk_concentration_symbols")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {(data.symbols ?? []).length === 0 ? (
                <p className="text-muted">{t("home.risk_concentration_empty")}</p>
              ) : (
                data.symbols.map((row) => (
                  <div key={row.symbol} className="rounded border border-border/60 p-3">
                    <p className="font-medium">{row.symbol}</p>
                    <p>{t("home.risk_gross_exposure")}: {formatCurrencyOrUnavailable(row.gross_exposure_usd)}</p>
                    <p>{t("home.risk_net_exposure")}: {formatCurrencyOrUnavailable(row.net_exposure_usd)}</p>
                    <p>{t("home.risk_sl_risk")}: {formatCurrencyOrUnavailable(row.sl_risk_usd)}</p>
                    <p className="text-muted text-xs">
                      {t("home.risk_contributors", {
                        portfolios: row.portfolio_count,
                        robots: row.robot_count,
                      })}
                    </p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>{t("home.risk_concentration_groups")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {(data.groups ?? []).length === 0 ? (
                <p className="text-muted">{t("home.risk_concentration_empty")}</p>
              ) : (
                data.groups.map((row) => (
                  <div key={row.group} className="rounded border border-border/60 p-3">
                    <p className="font-medium">{row.group}</p>
                    <p>{t("home.risk_gross_exposure")}: {formatCurrencyOrUnavailable(row.gross_exposure_usd)}</p>
                    <p>{t("home.risk_sl_risk")}: {formatCurrencyOrUnavailable(row.sl_risk_usd)}</p>
                    <p className="text-muted text-xs">
                      {t("home.risk_group_assets", { assets: row.assets.join(", ") })}
                    </p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </section>
  );
}
