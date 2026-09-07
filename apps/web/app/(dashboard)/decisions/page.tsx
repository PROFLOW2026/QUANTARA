import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { translateSignalReason } from "@/lib/display-text";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";

export default async function DecisionsPage() {
  let decisions = null;
  let error: string | null = null;

  try {
    decisions = await api.getDecisions();
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader titleKey="decisions.title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardHeader><CardTitle>{t("decisions.title")}</CardTitle></CardHeader>
        <CardContent>
          {!decisions?.length ? (
            <EmptyState message={t("decisions.empty")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("decisions.timestamp")}</TableHead>
                  <TableHead>{t("decisions.decision_type")}</TableHead>
                  <TableHead>{t("common.message")}</TableHead>
                  <TableHead>{t("common.strategy")}</TableHead>
                  <TableHead>{t("common.instrument")}</TableHead>
                  <TableHead>{t("decisions.candle_time")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {decisions.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell>{formatDateTime(d.timestamp)}</TableCell>
                    <TableCell><DecisionTypeBadge type={d.decision_type} /></TableCell>
                    <TableCell className="max-w-xs truncate">
                      {translateSignalReason(d.message)}
                    </TableCell>
                    <TableCell>{d.strategy_name ?? "—"}</TableCell>
                    <TableCell>{d.instrument ?? "—"}</TableCell>
                    <TableCell>{d.candle_time ? formatDateTime(d.candle_time) : "—"}</TableCell>
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
