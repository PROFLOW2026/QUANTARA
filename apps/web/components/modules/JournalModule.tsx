"use client";

import { ErrorBanner } from "@/components/layout/PageHeader";
import { DirectionBadge } from "@/components/trading/DirectionBadge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { PortfolioScopeBanner } from "@/components/trading/PortfolioScopeBanner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import { translateExitReason } from "@/lib/display-text";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { resolvePortfolioScope } from "@/lib/portfolio-scope";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPrice } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function JournalModule({ embedded, searchParams = {} }: ModuleProps) {
  const portfolioId = searchParams.portfolio_id;
  const { data, error, loading } = useModuleData(async () => {
    const portfolios = await api.getPortfolios();
    const scope = resolvePortfolioScope(portfolioId, portfolios, {
      redirectPath: "/journal",
    });
    const requests: Promise<unknown>[] = [
      api.getTrades(scope.portfolioId ? { portfolio_id: scope.portfolioId } : undefined),
    ];
    if (scope.portfolioId) {
      requests.push(api.getPortfolio(scope.portfolioId));
    }
    const results = await Promise.all(requests);
    return {
      scope,
      trades: results[0] as Awaited<ReturnType<typeof api.getTrades>>,
      portfolio: scope.portfolioId
        ? (results[1] as Awaited<ReturnType<typeof api.getPortfolio>>)
        : null,
    };
  }, [portfolioId]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const trades = data?.trades ?? [];

  return (
    <ModuleFrame embedded={embedded} titleKey="journal.title">
      <PortfolioScopeBanner
        portfolioId={data?.scope.portfolioId}
        portfolioName={data?.portfolio?.name}
        scopeAll={data?.scope.scopeAll}
      />
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      <Card>
        <CardHeader><CardTitle>{t("journal.title")}</CardTitle></CardHeader>
        <CardContent>
          {!trades.length ? (
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
                    <TableCell>{translateExitReason(tr.exit_reason)}</TableCell>
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
    </ModuleFrame>
  );
}
