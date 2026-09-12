"use client";

import { ErrorBanner } from "@/components/layout/PageHeader";
import { ModalLink } from "@/components/layout/ModalLink";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import { translateRiskProfile } from "@/lib/display-text";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

function groupPortfolios(items: NonNullable<Awaited<ReturnType<typeof api.getPortfolios>>>) {
  const groups = new Map<string, typeof items>();
  for (const item of items) {
    const key = item.robot_label ?? "Robot A";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return Array.from(groups.entries());
}

export default function PortfoliosModule({ embedded }: ModuleProps) {
  const { data: items, error, loading } = useModuleData(
    () => api.getPortfolios(),
    []
  );

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const grouped = items ? groupPortfolios(items) : [];

  return (
    <ModuleFrame embedded={embedded} titleKey="portfolio.title">
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      {!items?.length ? (
        <Card>
          <CardContent className="py-8">
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          </CardContent>
        </Card>
      ) : (
        grouped.map(([robotLabel, robotItems]) => (
          <Card key={robotLabel} className="mb-4">
            <CardHeader>
              <CardTitle>
                {robotLabel}
                {robotItems[0]?.strategy_name ? ` — ${robotItems[0].strategy_name}` : ""}
              </CardTitle>
              <p className="text-sm text-muted">
                {robotItems.length} {t("home.experiment_portfolios").toLowerCase()}
              </p>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {robotItems.map((item) => (
                <ModalLink
                  key={item.id}
                  href={`/portfolio?portfolio_id=${item.id}`}
                  className="rounded-lg border border-border bg-surface p-4 transition hover:border-accent/40"
                >
                  <p className="font-medium">{item.name}</p>
                  <p className="mt-1 text-xs text-muted">
                    {item.timeframe_he ?? item.timeframe ?? translateRiskProfile(item.risk_slug)}
                  </p>
                  <p className="mt-2 font-mono text-sm">{formatCurrency(item.equity)}</p>
                </ModalLink>
              ))}
            </CardContent>
          </Card>
        ))
      )}
    </ModuleFrame>
  );
}
