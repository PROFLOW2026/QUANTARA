import Link from "next/link";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api-client";
import { translateRiskProfile } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/utils";

export default async function PortfoliosPickerPage() {
  let items = null;
  let error: string | null = null;

  try {
    items = await api.getPortfolios();
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader titleKey="portfolio.title" />
      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t("portfolio.select")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {!items?.length ? (
            <p className="text-sm text-muted">{t("common.no_data")}</p>
          ) : (
            items.map((item) => (
              <Link
                key={item.id}
                href={`/portfolio?portfolio_id=${item.id}`}
                className="rounded-lg border border-border bg-surface-elevated/30 p-4 transition hover:border-accent/40"
              >
                <p className="font-medium">{item.name}</p>
                <p className="mt-1 text-xs text-muted">
                  {item.kind === "legacy"
                    ? t("portfolio.legacy")
                    : translateRiskProfile(item.risk_slug)}
                </p>
                <p className="mt-2 font-mono text-sm">{formatCurrency(item.equity)}</p>
              </Link>
            ))
          )}
        </CardContent>
      </Card>
    </>
  );
}
