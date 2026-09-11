"use client";

import { PageHeader, WorkerIndicator, EngineConnectionError } from "@/components/layout/PageHeader";
import { MetricCardCurrency } from "@/components/trading/MetricCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { LatestDecisionsPanel } from "@/components/trading/LatestDecisionsPanel";
import { StrategyFreshnessPanel } from "@/components/trading/StrategyFreshnessPanel";
import {
  ActiveAssetsTable,
  ProviderHealthPanel,
} from "@/components/trading/ActiveAssetsPanel";
import { BrokerAccountSummaryCards } from "@/components/trading/BrokerAccountSummaryCards";
import { ExposureRiskSummaryCards } from "@/components/trading/ExposureRiskSummaryCards";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useHomeDashboardPoll } from "@/hooks/useHomeDashboardPoll";
import { t } from "@/lib/i18n";
import { formatPercent } from "@/lib/utils";

export function HomeDashboard() {
  const {
    assetDecisions,
    today,
    workers,
    competition,
    assetAnalytics,
    brokerAccount,
    marketStatus,
    engineHealthy,
    engineConnectionError,
    competitionUnavailable,
    todayError,
    dataRefreshError,
    loading,
    refresh,
  } = useHomeDashboardPoll();

  const competitionReady = Boolean(competition?.active && !competitionUnavailable);
  const portfolios = competitionReady ? (competition?.portfolios ?? []) : [];
  const combinedEquity = competitionReady ? competition?.combined?.current_equity : null;
  const initialCapital = competitionReady
    ? (competition?.combined?.initial_equity ?? competition?.experiment?.total_initial_capital ?? null)
    : null;
  const combinedRealized = competitionReady
    ? portfolios.reduce((sum, row) => sum + row.realized_pnl, 0)
    : null;
  const combinedUnrealized = competitionReady
    ? portfolios.reduce((sum, row) => sum + row.unrealized_pnl, 0)
    : null;
  const combinedTotalPnl =
    combinedRealized != null && combinedUnrealized != null
      ? combinedRealized + combinedUnrealized
      : null;
  const strategyLegsOpen = competitionReady ? competition?.combined?.open_positions_total : null;
  const brokerPositionCount = brokerAccount?.broker_positions?.length ?? null;
  const closedTrades = competitionReady
    ? portfolios.reduce((sum, row) => sum + row.trades_count, 0)
    : null;
  const portfolioCount = competitionReady
    ? (competition?.experiment?.portfolio_count ?? portfolios.length)
    : null;
  const assetRows = assetAnalytics?.assets ?? [];
  const exposureSummary = assetAnalytics?.summary ?? null;
  const strategyRunner = workers?.workers?.find((w) => w.name === "strategy_runner");
  const freshness = workers?.strategy_freshness ?? strategyRunner?.freshness;
  const engineStatusHealthy = engineHealthy === true;

  return (
    <>
      <PageHeader titleKey="home.title" />

      {engineConnectionError ? (
        <div className="mb-4">
          <EngineConnectionError onRetry={() => void refresh()} />
        </div>
      ) : null}

      {dataRefreshError ? (
        <p className="mb-4 text-sm text-warning">{dataRefreshError}</p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.experiment_portfolios")}</CardTitle></CardHeader>
          <CardContent>
            {competitionUnavailable ? (
              <p className="text-sm text-muted">{t("common.section_unavailable")}</p>
            ) : (
              <p className="font-mono text-2xl">{portfolioCount ?? "—"}</p>
            )}
          </CardContent>
        </Card>
        {competitionUnavailable ? (
          <Card>
            <CardHeader><CardTitle>{t("home.competition_initial_capital")}</CardTitle></CardHeader>
            <CardContent><p className="text-sm text-muted">{t("common.section_unavailable")}</p></CardContent>
          </Card>
        ) : (
          <MetricCardCurrency
            label={t("home.competition_initial_capital")}
            value={initialCapital ?? 0}
          />
        )}
        {competitionUnavailable ? (
          <Card>
            <CardHeader><CardTitle>{t("home.competition_combined_equity")}</CardTitle></CardHeader>
            <CardContent><p className="text-sm text-muted">{t("common.section_unavailable")}</p></CardContent>
          </Card>
        ) : (
          <MetricCardCurrency
            label={t("home.competition_combined_equity")}
            value={combinedEquity ?? 0}
          />
        )}
        <Card>
          <CardHeader><CardTitle>{t("home.worker_status")}</CardTitle></CardHeader>
          <CardContent>
            <WorkerIndicator healthy={engineStatusHealthy} />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4">
        <h2 className="mb-2 text-sm font-medium text-muted">Paper Broker Account</h2>
        <BrokerAccountSummaryCards account={brokerAccount} loading={loading && !brokerAccount} />
      </div>

      <div className="mt-4">
        <ExposureRiskSummaryCards summary={exposureSummary} loading={loading && !exposureSummary} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>{t("home.realized_pnl")}</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <span className="text-muted">{t("common.loading")}</span>
            ) : competitionUnavailable || combinedRealized == null ? (
              <span className="text-muted">{t("common.section_unavailable")}</span>
            ) : (
              <PnLDisplay value={combinedRealized} size="lg" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.unrealized_pnl")}</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <span className="text-muted">{t("common.loading")}</span>
            ) : competitionUnavailable || combinedUnrealized == null ? (
              <span className="text-muted">{t("common.section_unavailable")}</span>
            ) : (
              <PnLDisplay value={combinedUnrealized} size="lg" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.total_pnl")}</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <span className="text-muted">{t("common.loading")}</span>
            ) : competitionUnavailable || combinedTotalPnl == null ? (
              <span className="text-muted">{t("common.section_unavailable")}</span>
            ) : (
              <PnLDisplay value={combinedTotalPnl} size="lg" />
            )}
          </CardContent>
        </Card>
      </div>

      {assetRows.length ? (
        <div className="mt-4">
          <ActiveAssetsTable assets={assetRows} />
        </div>
      ) : null}

      <div className="mt-4">
        <LatestDecisionsPanel decisions={assetDecisions} timeframe="5m" />
      </div>

      <div className="mt-4">
        <ProviderHealthPanel marketStatus={marketStatus} />
      </div>

      <div className="mt-4">
        <StrategyFreshnessPanel freshness={freshness} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.closed_trades")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{closedTrades}</p>
            <p className="mt-1 text-xs text-muted">{t("home.closed_trades_hint")}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Strategy Legs</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{strategyLegsOpen ?? "—"}</p>
            <p className="mt-1 text-xs text-muted">Attributed strategy positions across all robots</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Broker Positions</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{brokerPositionCount ?? "—"}</p>
            <p className="mt-1 text-xs text-muted">Net physical holdings in the shared paper broker account</p>
          </CardContent>
        </Card>

        {competition?.leader ? (
          <Card>
            <CardHeader><CardTitle>{t("home.competition_leader")}</CardTitle></CardHeader>
            <CardContent className="text-sm">
              <p>{competition.leader.name}</p>
              <p className="text-muted">{formatPercent(competition.leader.return_pct)}</p>
            </CardContent>
          </Card>
        ) : null}

        {competition?.worst_performer &&
        competition.worst_performer.portfolio_id !== competition.leader?.portfolio_id ? (
          <Card>
            <CardHeader><CardTitle>{t("home.worst_performer")}</CardTitle></CardHeader>
            <CardContent className="text-sm">
              <p>{competition.worst_performer.name}</p>
              <p className="text-muted">{formatPercent(competition.worst_performer.return_pct)}</p>
            </CardContent>
          </Card>
        ) : null}

        {competition?.leading_timeframe ? (
          <Card>
            <CardHeader><CardTitle>{t("home.competition_leading_timeframe")}</CardTitle></CardHeader>
            <CardContent className="text-sm">
              <p>
                {competition.leading_timeframe.title_he ??
                  competition.leading_timeframe.timeframe_he}
              </p>
              <p className="text-muted">
                {formatPercent(competition.leading_timeframe.average_return_pct)}
              </p>
            </CardContent>
          </Card>
        ) : null}

        <Card className="sm:col-span-2 lg:col-span-2">
          <CardHeader><CardTitle>{t("home.today_activity")}</CardTitle></CardHeader>
          <CardContent className="grid gap-1 text-sm sm:grid-cols-2">
            {todayError ? (
              <p className="text-muted sm:col-span-2">{todayError}</p>
            ) : (
              <>
                <p>{t("home.market_checks_today")}: {today?.market_checks_today ?? 0}</p>
                <p>{t("home.entry_signals_today")}: {today?.entry_signals_today ?? 0}</p>
                <p className="text-muted">{t("home.strategy_signals_today")}: {today?.strategy_signals_today ?? 0}</p>
                <p>{t("home.trades_opened_today")}: {today?.trades_opened_today ?? 0}</p>
                <p>{t("home.trades_closed_today")}: {today?.trades_closed_today ?? 0}</p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {competition?.timeframe_comparison?.length ? (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("home.timeframe_summary")}</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-3">
            {competition.timeframe_comparison.map((row) => (
              <div key={row.timeframe} className="rounded-md bg-surface-elevated p-3 text-sm">
                <p className="font-medium">{row.title_he ?? row.timeframe_he}</p>
                <p>
                  <span className="text-muted">{t("home.realized_pnl")}: </span>
                  <PnLDisplay value={row.realized_pnl ?? 0} size="sm" />
                </p>
                <p>
                  <span className="text-muted">{t("home.open_positions")}: </span>
                  {row.open_positions ?? 0}
                </p>
                <p>
                  <span className="text-muted">{t("home.closed_trades")}: </span>
                  {row.closed_trades ?? row.total_trades ?? 0}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {workers?.workers?.length ? (
        <Card className="mt-4">
          <CardHeader><CardTitle>{t("home.worker_timeframes")}</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            {(["5m", "15m", "1h", "orb_5m"] as const).map((timeframe) => {
              const tf = strategyRunner?.timeframes?.[timeframe];
              return (
                <div key={timeframe} className="rounded-md bg-surface-elevated p-3">
                  <p className="font-medium">{timeframe}</p>
                  <p>
                    <span className="text-muted">{t("home.worker_backlog")}: </span>
                    {tf?.backlog ?? "—"}
                  </p>
                  <p>
                    <span className="text-muted">{t("home.worker_status_label")}: </span>
                    {tf?.status ?? strategyRunner?.execution_status ?? "—"}
                  </p>
                </div>
              );
            })}
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}
