import Link from "next/link";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
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
      <p className="mb-4 font-mono text-sm text-muted">{params.slug}</p>
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardContent className="pt-4">
          {!versions?.length ? (
            <EmptyState message={t("common.no_data")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.version")}</TableHead>
                  <TableHead>{t("common.status")}</TableHead>
                  <TableHead>{t("common.created")}</TableHead>
                  <TableHead>{t("strategies.parameters")}</TableHead>
                  <TableHead>{t("strategies.trades_count")}</TableHead>
                  <TableHead>{t("strategies.backtests_count")}</TableHead>
                  <TableHead>{t("strategies.logic_hash")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell className="font-mono">{v.version}</TableCell>
                    <TableCell><StatusBadge status={v.status} /></TableCell>
                    <TableCell>{formatDateTime(v.created_at)}</TableCell>
                    <TableCell>
                      {v.parameters ? (
                        <pre className="max-w-xs overflow-x-auto rounded bg-surface-elevated p-2 text-xs">
                          {JSON.stringify(v.parameters, null, 2)}
                        </pre>
                      ) : "—"}
                    </TableCell>
                    <TableCell>{v.trades_count}</TableCell>
                    <TableCell>{v.backtests_count}</TableCell>
                    <TableCell className="font-mono text-xs">{v.logic_hash ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
