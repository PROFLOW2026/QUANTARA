"use client";

import { Fragment, useState } from "react";
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
import {
  groupResearchDecisions,
  isMultiTierDecisionGroup,
} from "@/lib/decision-grouping";
import {
  translateDecisionMessage,
  translateRobotStrategyLabel,
  translateRiskProfile,
} from "@/lib/display-text";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { resolvePortfolioScope } from "@/lib/portfolio-scope";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

function TierDrilldown({
  members,
}: {
  members: Awaited<ReturnType<typeof api.getDecisions>>;
}) {
  return (
    <div className="space-y-2 py-2">
      {members.map((member) => (
        <div
          key={member.id}
          className="rounded-md border border-border/40 bg-surface-inner px-3 py-2 text-xs"
        >
          <p className="font-medium">
            {member.risk_name_he ??
              (member.risk_slug ? translateRiskProfile(member.risk_slug) : member.portfolio_name ?? "—")}
          </p>
          {member.portfolio_name ? (
            <p className="mt-0.5 text-muted">{member.portfolio_name}</p>
          ) : null}
          <p className="mt-1 text-muted">{formatDateTime(member.timestamp)}</p>
          <p className="mt-1">{translateDecisionMessage(member)}</p>
        </div>
      ))}
    </div>
  );
}

export default function DecisionsModule({ embedded, searchParams = {} }: ModuleProps) {
  const portfolioId = searchParams.portfolio_id;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

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
  const groupEnabled = Boolean(data?.scope.scopeAll);
  const groups = groupEnabled
    ? groupResearchDecisions(decisions)
    : decisions.map((decision) => ({
        key: decision.id,
        representative: decision,
        members: [decision],
      }));

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
        <CardHeader>
          <CardTitle>{t("decisions.title")}</CardTitle>
          {groupEnabled ? (
            <p className="text-xs text-muted">{t("decisions.grouped_hint")}</p>
          ) : null}
        </CardHeader>
        <CardContent>
          {!groups.length ? (
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
                  <TableHead>{t("decisions.portfolios")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map((group) => {
                  const d = group.representative;
                  const historical = isHistoricalIncidentDecision(d);
                  const multi = isMultiTierDecisionGroup(group);
                  const isOpen = Boolean(expanded[group.key]);

                  return (
                    <Fragment key={group.key}>
                      <TableRow className={historical ? "opacity-70" : undefined}>
                        <TableCell>{formatDateTime(d.timestamp)}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-2">
                            <DecisionTypeBadge type={d.decision_type} metadata={d.metadata} />
                            {historical ? (
                              <Badge variant="muted">{t("decisions.historical_incident")}</Badge>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell className="max-w-xs whitespace-pre-wrap break-words">
                          {translateDecisionMessage(d)}
                        </TableCell>
                        <TableCell>
                          {translateRobotStrategyLabel(d.robot_label, d.strategy_name, d.strategy_slug)}
                        </TableCell>
                        <TableCell>{d.instrument ?? "—"}</TableCell>
                        <TableCell>{d.candle_time ? formatDateTime(d.candle_time) : "—"}</TableCell>
                        <TableCell>
                          {multi ? (
                            <button
                              type="button"
                              className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                              aria-expanded={isOpen}
                              onClick={() =>
                                setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }))
                              }
                            >
                              <Badge variant="outline">
                                {t("decisions.portfolio_tier_count", { count: group.members.length })}
                              </Badge>
                              <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                            </button>
                          ) : (
                            <span className="text-xs text-muted">1</span>
                          )}
                        </TableCell>
                      </TableRow>
                      {multi && isOpen ? (
                        <TableRow key={`${group.key}-tiers`}>
                          <TableCell colSpan={7}>
                            <TierDrilldown members={group.members} />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
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
