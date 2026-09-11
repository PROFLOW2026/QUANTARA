"use client";

import { ErrorBanner, ChartPlaceholder } from "@/components/layout/PageHeader";
import { MetricCardCurrency } from "@/components/trading/MetricCard";
import { PortfolioScopeBanner } from "@/components/trading/PortfolioScopeBanner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { useModuleData } from "@/hooks/useModuleData";
import { api } from "@/lib/api-client";
import { translateRiskProfile } from "@/lib/display-text";
import type { ModuleProps } from "@/lib/modal-workspace/types";
import { canonicalizePortfolioId, resolvePortfolioScope } from "@/lib/portfolio-scope";
import { t } from "@/lib/i18n";
import { formatCurrency, formatDateTime, formatPercent } from "@/lib/utils";
import { ModuleFrame } from "./ModuleFrame";

export default function PortfolioModule({ embedded, searchParams = {} }: ModuleProps) {
  const portfolioIdParam = searchParams.portfolio_id;
  const { data, error, loading } = useModuleData(async () => {
    const portfolios = await api.getPortfolios();
    const scope = resolvePortfolioScope(portfolioIdParam, portfolios, {
      requireSelection: true,
    });
    const portfolioId = scope.portfolioId;
    if (!portfolioId) {
      return { portfolioId: undefined, portfolio: null, snapshots: null };
    }
    const resolved = canonicalizePortfolioId(portfolioId);
    const [portfolio, snapshots] = await Promise.all([
      api.getPortfolio(resolved),
      api.getSnapshots(resolved),
    ]);
    return { portfolioId: resolved, portfolio, snapshots };
  }, [portfolioIdParam]);

  if (loading) {
    return <p className="text-sm text-muted">{t("common.loading")}</p>;
  }

  const portfolio = data?.portfolio;

  return (
    <ModuleFrame embedded={embedded} titleKey="portfolio.title">
      <PortfolioScopeBanner
        portfolioId={data?.portfolioId}
        portfolioName={portfolio?.name}
        competition={portfolio?.competition}
      />

      {portfolio?.competition ? (
        <Card className="mb-4">
          <CardContent className="grid gap-2 pt-6 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-muted">{t("portfolio.timeframe_label")}</p>
              <p>{portfolio.competition.timeframe_he}</p>
            </div>
            <div>
              <p className="text-muted">{t("portfolio.risk_level_label")}</p>
              <p>{portfolio.competition.risk_name_he}</p>
            </div>
            <div>
              <p className="text-muted">{t("portfolio.risk_per_trade_label")}</p>
              <p>{portfolio.competition.risk_per_trade_pct.toFixed(2)}%</p>
            </div>
            <div>
              <p className="text-muted">{t("portfolio.initial_capital")}</p>
              <p>{formatCurrency(portfolio.initial_capital ?? 0)}</p>
            </div>
          </CardContent>
        </Card>
      ) : null}
      {error ? (
        <div className="mb-4"><ErrorBanner message={error} /></div>
      ) : null}

      {!data?.portfolioId ? (
        <Card>
          <CardContent className="py-8">
            <p className="text-sm text-muted">{t("portfolio.switch")}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <Card className="mb-4">
            <CardHeader><CardTitle>{t("portfolio.equity_chart")}</CardTitle></CardHeader>
            <CardContent>
              <ChartPlaceholder label={t("portfolio.equity_chart")} />
            </CardContent>
          </Card>

          <div className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCardCurrency label={t("portfolio.cash_balance")} value={portfolio?.cash_balance ?? 0} />
            <MetricCardCurrency label={t("portfolio.unrealized_pnl")} value={portfolio?.unrealized_pnl ?? 0} />
            <MetricCardCurrency label={t("portfolio.realized_pnl")} value={portfolio?.realized_pnl ?? 0} />
            <MetricCardCurrency label={t("portfolio.peak_equity")} value={portfolio?.peak_equity ?? 0} />
          </div>

          <div className="mb-4 grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader><CardTitle>{t("portfolio.current_drawdown")}</CardTitle></CardHeader>
              <CardContent>
                <p className="font-mono text-xl text-loss">
                  {formatPercent(-(portfolio?.current_drawdown_pct ?? 0))}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("portfolio.risk_profile")}</CardTitle></CardHeader>
              <CardContent><p>{translateRiskProfile(portfolio?.risk_profile)}</p></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("portfolio.mode")}</CardTitle></CardHeader>
              <CardContent><p>{portfolio?.mode ?? t("common.paper")}</p></CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader><CardTitle>{t("portfolio.snapshots")}</CardTitle></CardHeader>
            <CardContent>
              {!data.snapshots?.length ? (
                <EmptyState message={t("common.no_data")} />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("portfolio.snapshot_time")}</TableHead>
                      <TableHead>{t("portfolio.equity")}</TableHead>
                      <TableHead>{t("portfolio.cash_balance")}</TableHead>
                      <TableHead>{t("portfolio.unrealized_pnl")}</TableHead>
                      <TableHead>{t("portfolio.realized_pnl")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.snapshots.map((s) => (
                      <TableRow key={s.id}>
                        <TableCell>{formatDateTime(s.timestamp)}</TableCell>
                        <TableCell className="font-mono">{formatCurrency(s.equity)}</TableCell>
                        <TableCell className="font-mono">{formatCurrency(s.cash_balance)}</TableCell>
                        <TableCell className="font-mono">{formatCurrency(s.unrealized_pnl)}</TableCell>
                        <TableCell className="font-mono">{formatCurrency(s.realized_pnl)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </ModuleFrame>
  );
}
