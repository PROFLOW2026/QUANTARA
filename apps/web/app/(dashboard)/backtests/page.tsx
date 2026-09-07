import Link from "next/link";
import { PageHeader, ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPercent } from "@/lib/utils";

export default async function BacktestsPage() {
  let backtests = null;
  let error: string | null = null;

  try {
    backtests = await api.getBacktests();
  } catch (e) {
    error = e instanceof ApiError ? e.message : t("common.error");
  }

  return (
    <>
      <PageHeader
        titleKey="backtests.title"
        action={
          <button className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90">
            {t("backtests.new_backtest")}
          </button>
        }
      />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <Card>
        <CardContent className="pt-4">
          {!backtests?.length ? (
            <EmptyState message={t("backtests.empty")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.name")}</TableHead>
                  <TableHead>{t("common.strategy")}</TableHead>
                  <TableHead>{t("common.period")}</TableHead>
                  <TableHead>{t("common.status")}</TableHead>
                  <TableHead>{t("backtests.return")}</TableHead>
                  <TableHead>{t("backtests.win_rate")}</TableHead>
                  <TableHead>{t("backtests.max_dd")}</TableHead>
                  <TableHead>{t("backtests.trades")}</TableHead>
                  <TableHead>{t("common.created")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {backtests.map((bt) => (
                  <TableRow key={bt.id}>
                    <TableCell>
                      <Link href={`/backtests/${bt.id}`} className="text-accent hover:underline">
                        {bt.name ?? bt.id.slice(0, 8)}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs">
                      {bt.strategy_name} v{bt.strategy_version}
                    </TableCell>
                    <TableCell className="text-xs">
                      {formatDateTime(bt.period_start)} — {formatDateTime(bt.period_end)}
                    </TableCell>
                    <TableCell><StatusBadge status={bt.status} /></TableCell>
                    <TableCell className="font-mono">
                      {bt.return_pct != null ? formatPercent(bt.return_pct) : "—"}
                    </TableCell>
                    <TableCell>{bt.win_rate != null ? `${bt.win_rate.toFixed(1)}%` : "—"}</TableCell>
                    <TableCell className="font-mono text-loss">
                      {bt.max_drawdown_pct != null ? formatPercent(-bt.max_drawdown_pct) : "—"}
                    </TableCell>
                    <TableCell>{bt.trades_count ?? "—"}</TableCell>
                    <TableCell>{formatDateTime(bt.created_at)}</TableCell>
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
