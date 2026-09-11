"use client";

import { ErrorBanner, StatusBadge } from "@/components/layout/PageHeader";
import { ModalLink } from "@/components/layout/ModalLink";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPercent } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function BacktestsModule({ embedded }: ModuleProps) {
  const { data: backtests, error, loading } = useModuleData(
    () => api.getBacktests(),
    []
  );

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  return (
    <ModuleFrame
      embedded={embedded}
      titleKey="backtests.title"
      action={
        !embedded ? (
          <button className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90">
            {t("backtests.new_backtest")}
          </button>
        ) : undefined
      }
    >
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

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
                      <ModalLink href={`/backtests/${bt.id}`} className="text-accent hover:underline">
                        {bt.name ?? bt.id.slice(0, 8)}
                      </ModalLink>
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
    </ModuleFrame>
  );
}
