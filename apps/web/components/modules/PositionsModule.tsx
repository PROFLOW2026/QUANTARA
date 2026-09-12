"use client";

import { useState } from "react";
import { ErrorBanner } from "@/components/layout/PageHeader";
import { DirectionBadge } from "@/components/trading/DirectionBadge";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { PortfolioScopeBanner } from "@/components/trading/PortfolioScopeBanner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { resolvePortfolioScope } from "@/lib/portfolio-scope";
import { t } from "@/lib/i18n";
import { formatDateTime, formatPrice } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function PositionsModule({ embedded, searchParams = {} }: ModuleProps) {
  const portfolioId = searchParams.portfolio_id;
  const [view, setView] = useState<"legs" | "broker">("legs");

  const { data, error, loading } = useModuleData(async () => {
    const portfolios = await api.getPortfolios();
    const scope = resolvePortfolioScope(portfolioId, portfolios, {
      redirectPath: "/positions",
    });
    const requests: Promise<unknown>[] = [
      api.getPositions("open", scope.portfolioId),
      api.getBrokerAccount(),
    ];
    if (scope.portfolioId) {
      requests.push(api.getPortfolio(scope.portfolioId));
    }
    const results = await Promise.all(requests);
    return {
      scope,
      positions: results[0] as Awaited<ReturnType<typeof api.getPositions>>,
      brokerAccount: results[1] as Awaited<ReturnType<typeof api.getBrokerAccount>>,
      portfolio: scope.portfolioId
        ? (results[2] as Awaited<ReturnType<typeof api.getPortfolio>>)
        : null,
    };
  }, [portfolioId]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const positions = data?.positions ?? [];
  const brokerPositions = data?.brokerAccount?.broker_positions ?? [];

  return (
    <ModuleFrame embedded={embedded} titleKey="positions.title">
      <PortfolioScopeBanner
        portfolioId={data?.scope.portfolioId}
        portfolioName={data?.portfolio?.name}
        scopeAll={data?.scope.scopeAll}
      />
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      <Tabs
        tabs={[
          { id: "legs", label: t("positions.strategy_legs_tab") },
          { id: "broker", label: t("positions.broker_positions_tab") },
        ]}
        active={view}
        onChange={(id) => setView(id as "legs" | "broker")}
        className="mb-4"
      />

      {view === "legs" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("positions.strategy_legs_title")}</CardTitle>
          </CardHeader>
          <CardContent>
            {!positions.length ? (
              <EmptyState message={t("positions.empty")} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("common.instrument")}</TableHead>
                    <TableHead>{t("common.direction")}</TableHead>
                    <TableHead>{t("positions.size")}</TableHead>
                    <TableHead>{t("positions.entry")}</TableHead>
                    <TableHead>{t("positions.current")}</TableHead>
                    <TableHead>{t("positions.sl_tp")}</TableHead>
                    <TableHead>{t("positions.pnl")}</TableHead>
                    <TableHead>{t("positions.duration")}</TableHead>
                    <TableHead>{t("common.strategy")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {positions.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell>{p.instrument}</TableCell>
                      <TableCell><DirectionBadge direction={p.direction} /></TableCell>
                      <TableCell className="font-mono">{p.size}</TableCell>
                      <TableCell>
                        <span className="font-mono">{formatPrice(p.entry_price)}</span>
                        <br />
                        <span className="text-xs text-muted">{formatDateTime(p.entry_time)}</span>
                      </TableCell>
                      <TableCell className="font-mono">{formatPrice(p.current_price)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {p.stop_loss != null ? (
                          <div className="space-y-0.5">
                            <p>
                              <span className="text-muted">{t("positions.stop")}: </span>
                              {formatPrice(p.stop_loss)}
                            </p>
                            {p.take_profit != null ? (
                              <p>
                                <span className="text-muted">{t("positions.target")}: </span>
                                {formatPrice(p.take_profit)}
                              </p>
                            ) : null}
                          </div>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell>
                        <PnLDisplay value={p.unrealized_pnl} showPercent={p.unrealized_pnl_pct} size="sm" />
                      </TableCell>
                      <TableCell>{p.duration ?? "—"}</TableCell>
                      <TableCell className="text-xs">
                        {p.strategy_name ?? "—"}
                        {p.strategy_version && ` v${p.strategy_version}`}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{t("positions.broker_positions_title")}</CardTitle>
          </CardHeader>
          <CardContent>
            {data?.scope.scopeAll === false ? (
              <p className="text-sm text-muted">{t("positions.broker_scope_hint")}</p>
            ) : !brokerPositions.length ? (
              <EmptyState message={t("positions.broker_empty")} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("common.instrument")}</TableHead>
                    <TableHead>Net Qty</TableHead>
                    <TableHead>Avg Entry</TableHead>
                    <TableHead>Mark</TableHead>
                    <TableHead>{t("positions.pnl")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {brokerPositions.map((p) => (
                    <TableRow key={p.symbol}>
                      <TableCell>{p.symbol}</TableCell>
                      <TableCell className="font-mono">
                        {p.net_quantity > 0 ? "+" : ""}
                        {p.net_quantity}
                      </TableCell>
                      <TableCell className="font-mono">{formatPrice(p.average_price)}</TableCell>
                      <TableCell className="font-mono">{formatPrice(p.mark_price)}</TableCell>
                      <TableCell>
                        <PnLDisplay value={p.unrealized_pnl} size="sm" />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </ModuleFrame>
  );
}
