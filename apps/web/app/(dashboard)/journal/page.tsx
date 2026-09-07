import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { DirectionBadge } from "@/components/trading/DirectionBadge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPrice } from "@/lib/utils";

export default async function JournalPage() {
  let trades = null;
  let error: string | null = null;

  try {
    trades = await api.getTrades({ status: "closed" });
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader titleKey="journal.title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardHeader><CardTitle>{t("journal.title")}</CardTitle></CardHeader>
        <CardContent>
          {!trades?.length ? (
            <EmptyState message={t("journal.empty")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.date")}</TableHead>
                  <TableHead>{t("common.instrument")}</TableHead>
                  <TableHead>{t("common.direction")}</TableHead>
                  <TableHead>{t("journal.entry_exit")}</TableHead>
                  <TableHead>{t("positions.pnl")}</TableHead>
                  <TableHead>{t("positions.duration")}</TableHead>
                  <TableHead>{t("journal.exit_reason")}</TableHead>
                  <TableHead>{t("common.version")}</TableHead>
                  <TableHead>{t("journal.fees")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((tr) => (
                  <TableRow key={tr.id}>
                    <TableCell>{formatDateTime(tr.close_time)}</TableCell>
                    <TableCell>{tr.instrument}</TableCell>
                    <TableCell><DirectionBadge direction={tr.direction} /></TableCell>
                    <TableCell className="font-mono text-xs">
                      {formatPrice(tr.entry_price)} → {formatPrice(tr.exit_price)}
                    </TableCell>
                    <TableCell><PnLDisplay value={tr.pnl} size="sm" /></TableCell>
                    <TableCell>{tr.duration ?? "—"}</TableCell>
                    <TableCell>{tr.exit_reason ?? "—"}</TableCell>
                    <TableCell className="text-xs">
                      {tr.strategy_name ?? "—"}
                      {tr.strategy_version && ` v${tr.strategy_version}`}
                    </TableCell>
                    <TableCell className="font-mono">{tr.fees ?? "—"}</TableCell>
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
