"use client";

import { ErrorBanner } from "@/components/layout/PageHeader";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import { PortfolioScopeBanner } from "@/components/trading/PortfolioScopeBanner";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import { isHistoricalIncidentDecision } from "@/lib/decision-history";
import { translateDecisionMessage } from "@/lib/display-text";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { resolvePortfolioScope } from "@/lib/portfolio-scope";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function DecisionsModule({ embedded, searchParams = {} }: ModuleProps) {
  const portfolioId = searchParams.portfolio_id;
  const { data, error, loading } = useModuleData(async () => {
    const portfolios = await api.getPortfolios();
    const scope = resolvePortfolioScope(portfolioId, portfolios, {
      redirectPath: "/decisions",
    });
    const requests: Promise<unknown>[] = [
      api.getDecisions(
        scope.portfolioId ? { portfolio_id: scope.portfolioId } : undefined
      ),
    ];
    if (scope.portfolioId) {
      requests.push(api.getPortfolio(scope.portfolioId));
    }
    const results = await Promise.all(requests);
    return {
      scope,
      decisions: results[0] as Awaited<ReturnType<typeof api.getDecisions>>,
      portfolio: scope.portfolioId
        ? (results[1] as Awaited<ReturnType<typeof api.getPortfolio>>)
        : null,
    };
  }, [portfolioId]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const decisions = data?.decisions ?? [];

  return (
    <ModuleFrame embedded={embedded} titleKey="decisions.title">
      <PortfolioScopeBanner
        portfolioId={data?.scope.portfolioId}
        portfolioName={data?.portfolio?.name}
        scopeAll={data?.scope.scopeAll}
        competition={data?.portfolio?.competition}
      />
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      <Card>
        <CardHeader><CardTitle>{t("decisions.title")}</CardTitle></CardHeader>
        <CardContent>
          {!decisions.length ? (
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
                {decisions.map((d) => {
                  const historical = isHistoricalIncidentDecision(d);
                  return (
                    <TableRow
                      key={d.id}
                      className={historical ? "opacity-70" : undefined}
                    >
                      <TableCell>{formatDateTime(d.timestamp)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-2">
                          <DecisionTypeBadge type={d.decision_type} metadata={d.metadata} />
                          {historical ? (
                            <Badge variant="muted">{t("decisions.historical_incident")}</Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="max-w-xs truncate">
                        {translateDecisionMessage(d)}
                      </TableCell>
                      <TableCell>{d.strategy_name ?? "—"}</TableCell>
                      <TableCell>{d.instrument ?? "—"}</TableCell>
                      <TableCell>{d.candle_time ? formatDateTime(d.candle_time) : "—"}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </ModuleFrame>
  );
}
