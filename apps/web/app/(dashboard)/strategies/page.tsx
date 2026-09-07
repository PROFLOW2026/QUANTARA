import Link from "next/link";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { translateTimeframe } from "@/lib/display-text";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";

export default async function StrategiesPage() {
  let strategies = null;
  let error: string | null = null;

  try {
    strategies = await api.getStrategies();
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader titleKey="strategies.title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardHeader><CardTitle>{t("strategies.title")}</CardTitle></CardHeader>
        <CardContent>
          {!strategies?.length ? (
            <EmptyState message={t("strategies.empty")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.name")}</TableHead>
                  <TableHead>{t("strategies.slug")}</TableHead>
                  <TableHead>{t("common.status")}</TableHead>
                  <TableHead>{t("strategies.versions")}</TableHead>
                  <TableHead>{t("strategies.instruments")}</TableHead>
                  <TableHead>{t("strategies.timeframes")}</TableHead>
                  <TableHead>{t("strategies.active_instances")}</TableHead>
                  <TableHead>{t("common.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategies.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-medium">{s.name}</TableCell>
                    <TableCell className="font-mono text-xs">{s.slug}</TableCell>
                    <TableCell><StatusBadge status={s.status} /></TableCell>
                    <TableCell>{s.versions_count}</TableCell>
                    <TableCell>{s.instruments?.join(", ") ?? "—"}</TableCell>
                    <TableCell>
                      {s.timeframes?.map((tf) => translateTimeframe(tf)).join(", ") ?? "—"}
                    </TableCell>
                    <TableCell>{s.active_instances}</TableCell>
                    <TableCell>
                      <Link
                        href={`/strategies/${s.slug}/versions`}
                        className="text-sm text-accent hover:underline"
                      >
                        {t("strategies.view_versions")}
                      </Link>
                    </TableCell>
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
