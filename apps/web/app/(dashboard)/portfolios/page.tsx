import Link from "next/link";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api-client";
import { translateRiskProfile } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/utils";

function groupPortfolios(items: NonNullable<Awaited<ReturnType<typeof api.getPortfolios>>>) {
  const groups = new Map<string, typeof items>();
  for (const item of items) {
    const key = item.robot_label ?? "Robot A";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return Array.from(groups.entries());
}

export default async function PortfoliosPickerPage() {
  let items = null;
  let error: string | null = null;

  try {
    items = await api.getPortfolios();
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  const grouped = items ? groupPortfolios(items) : [];

  return (
    <>
      <PageHeader titleKey="portfolio.title" />
      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} />
        </div>
      )}

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
                <Link
                  key={item.id}
                  href={`/portfolio?portfolio_id=${item.id}`}
                  className="rounded-lg border border-border bg-surface-elevated/30 p-4 transition hover:border-accent/40"
                >
                  <p className="font-medium">{item.name}</p>
                  <p className="mt-1 text-xs text-muted">
                    {item.timeframe_he ?? item.timeframe ?? translateRiskProfile(item.risk_slug)}
                  </p>
                  <p className="mt-2 font-mono text-sm">{formatCurrency(item.equity)}</p>
                </Link>
              ))}
            </CardContent>
          </Card>
        ))
      )}
    </>
  );
}
