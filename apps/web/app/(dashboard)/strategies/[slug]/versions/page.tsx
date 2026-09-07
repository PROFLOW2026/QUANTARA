import Link from "next/link";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { StrategyParametersSummary } from "@/components/trading/StrategyParametersSummary";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { shortenHash } from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";

interface Props {
  params: { slug: string };
}

export default async function StrategyVersionsPage({ params }: Props) {
  let versions = null;
  let error: string | null = null;

  try {
    versions = await api.getStrategyVersions(params.slug);
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader
        titleKey="strategies.versions_title"
        action={
          <Link href="/strategies" className="text-sm text-accent hover:underline">
            ← {t("common.back")}
          </Link>
        }
      />
      <p className="mb-4 text-sm text-muted">{params.slug}</p>
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      {!versions?.length ? (
        <EmptyState message={t("common.no_data")} />
      ) : (
        <div className="space-y-4">
          {versions.map((v) => (
            <Card key={v.id}>
              <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle className="font-mono">{v.version}</CardTitle>
                  <p className="mt-1 text-sm text-muted">
                    {t("common.created")}: {formatDateTime(v.created_at)}
                  </p>
                </div>
                <StatusBadge status={v.status} />
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <h3 className="mb-2 text-sm font-medium text-slate-200">
                    {t("strategies.parameters_summary")}
                  </h3>
                  <StrategyParametersSummary parameters={v.parameters} />
                </div>

                <div className="grid gap-3 sm:grid-cols-2 text-sm">
                  <div>
                    <span className="text-muted">{t("strategies.trades_count")}: </span>
                    {v.trades_count}
                  </div>
                  <div>
                    <span className="text-muted">{t("strategies.backtests_count")}: </span>
                    {v.backtests_count}
                  </div>
                </div>

                {v.parameters ? (
                  <details className="rounded-md border border-border bg-surface-elevated/30 p-3">
                    <summary className="cursor-pointer text-sm text-accent">
                      {t("common.show_advanced")}
                    </summary>
                    <div className="mt-3 space-y-2 text-xs">
                      <p>
                        <span className="text-muted">{t("strategies.logic_hash")}: </span>
                        <span className="font-mono">{shortenHash(v.logic_hash)}</span>
                      </p>
                      <pre className="overflow-x-auto text-muted">
                        {JSON.stringify(v.parameters, null, 2)}
                      </pre>
                    </div>
                  </details>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
