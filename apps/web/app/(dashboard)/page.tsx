"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader, WorkerIndicator, EngineConnectionError } from "@/components/layout/PageHeader";
import { MetricCardCurrency } from "@/components/trading/MetricCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { PriceDisplay } from "@/components/trading/PriceDisplay";
import { LatestDecisionsPanel } from "@/components/trading/LatestDecisionsPanel";
import {
  ActiveAssetsSummary,
  ActiveAssetsTable,
  AssetResultsTable,
  ProviderHealthPanel,
} from "@/components/trading/ActiveAssetsPanel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  api,
  ApiError,
  isEngineConnectionError,
  type Decision,
  type CandleLatest,
  type WorkerStatus,
  type TodayActivity,
  type AssetAnalyticsResponse,
  type MarketProviderStatus,
} from "@/lib/api-client";
import { loadCompetitionView } from "@/lib/competition-client";
import { t } from "@/lib/i18n";
import { formatPercent, formatRelativeTime, formatCurrency } from "@/lib/utils";

const POLL_INTERVAL = 60_000;

export default function HomePageClient() {
  const [gold, setGold] = useState<CandleLatest | null>(null);
  const [assetDecisions, setAssetDecisions] = useState<Decision[]>([]);
  const [today, setToday] = useState<TodayActivity | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const [competition, setCompetition] = useState<Awaited<
    ReturnType<typeof loadCompetitionView>
  > | null>(null);
  const [assetAnalytics, setAssetAnalytics] = useState<AssetAnalyticsResponse | null>(null);
  const [marketStatus, setMarketStatus] = useState<MarketProviderStatus | null>(null);
  const [engineConnectionError, setEngineConnectionError] = useState(false);
  const [goldError, setGoldError] = useState<string | null>(null);
  const [todayError, setTodayError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const results = await Promise.allSettled([
        api.getWorkersStatus(),
        api.getCandlesLatest(),
        api.getDecisionsByAsset("5m"),
        api.getAnalyticsToday(),
        loadCompetitionView(),
        api.getAssetAnalytics(),
        api.getMarketStatus(),
      ]);

      const coreResult = results[0];
      const coreFailed =
        coreResult.status === "rejected" &&
        isEngineConnectionError((coreResult as PromiseRejectedResult).reason);
      setEngineConnectionError(coreFailed);

      if (coreResult.status === "fulfilled") {
        setWorkers(coreResult.value);
      } else {
        setWorkers(null);
      }

      if (results[1].status === "fulfilled") {
        setGold(results[1].value);
        setGoldError(null);
      } else {
        setGold(null);
        const reason = (results[1] as PromiseRejectedResult).reason;
        setGoldError(
          reason instanceof ApiError
            ? `${t("home.gold_load_error")} (${reason.status})`
            : t("home.gold_load_error")
        );
      }

      if (results[2].status === "fulfilled") {
        setAssetDecisions(results[2].value.decisions ?? []);
      } else {
        setAssetDecisions([]);
      }

      if (results[3].status === "fulfilled") {
        setToday(results[3].value);
        setTodayError(null);
      } else {
        setToday(null);
        setTodayError(t("common.section_unavailable"));
      }

      if (results[4].status === "fulfilled") setCompetition(results[4].value);
      else setCompetition(null);

      if (results[5].status === "fulfilled") setAssetAnalytics(results[5].value);
      else setAssetAnalytics(null);

      if (results[6].status === "fulfilled") setMarketStatus(results[6].value);
      else setMarketStatus(null);
    } catch (e) {
      setEngineConnectionError(isEngineConnectionError(e));
      setGoldError(null);
      setTodayError(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll(true);
    const id = setInterval(() => {
      void fetchAll(false);
    }, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchAll]);

  const portfolios = competition?.portfolios ?? [];
  const combinedEquity = competition?.combined?.current_equity ?? 0;
  const initialCapital = competition?.experiment?.total_initial_capital ?? 30_000;
  const combinedRealized = portfolios.reduce((sum, row) => sum + row.realized_pnl, 0);
  const combinedUnrealized = portfolios.reduce((sum, row) => sum + row.unrealized_pnl, 0);
  const combinedTotalPnl = combinedRealized + combinedUnrealized;
  const openPositions = competition?.combined?.open_positions_total ?? 0;
  const closedTrades = portfolios.reduce((sum, row) => sum + row.trades_count, 0);
  const portfolioCount = competition?.experiment?.portfolio_count ?? portfolios.length;
  const assetRows = assetAnalytics?.assets ?? [];
  const activeProviders = ["Twelve Data", "Tiingo", "Alpaca"];

  return (
    <>
      <PageHeader titleKey="home.title" />

      {engineConnectionError ? (
        <div className="mb-4">
          <EngineConnectionError onRetry={() => void fetchAll(true)} />
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.experiment_portfolios")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{portfolioCount}</p>
            <p className="mt-1 text-xs text-muted">{t("home.competition_card_title")}</p>
          </CardContent>
        </Card>
        <MetricCardCurrency
          label={t("home.competition_initial_capital")}
          hint={t("home.equity_hint")}
          value={initialCapital}
        />
        <MetricCardCurrency
          label={t("home.competition_combined_equity")}
          value={combinedEquity}
        />
        <Card>
          <CardHeader><CardTitle>{t("home.worker_status")}</CardTitle></CardHeader>
          <CardContent>
            <WorkerIndicator healthy={workers?.healthy ?? false} />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader><CardTitle>{t("home.realized_pnl")}</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <span className="text-muted">{t("common.loading")}</span>
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
            ) : (
              <PnLDisplay value={combinedTotalPnl} size="lg" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.open_positions")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{openPositions}</p>
            <Link
              href="/portfolio-comparison"
              className="mt-3 inline-block text-sm text-accent hover:underline"
            >
              {t("home.competition_view")} →
            </Link>
          </CardContent>
        </Card>
      </div>

      <div className="mt-4">
        <ActiveAssetsSummary
          assetsActive={assetAnalytics?.assets_active ?? 8}
          providers={activeProviders}
        />
      </div>

      {assetRows.length ? (
        <div className="mt-4 space-y-4">
          <ActiveAssetsTable assets={assetRows} />
          <AssetResultsTable assets={assetRows} />
        </div>
      ) : null}

      <div className="mt-4">
        <ProviderHealthPanel marketStatus={marketStatus} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle>{t("home.gold_snapshot")}</CardTitle></CardHeader>
          <CardContent>
            {gold ? (
              <>
                <PriceDisplay
                  value={gold.price}
                  change={gold.change}
                  changePct={gold.change_pct}
                  size="lg"
                />
                <p className="mt-2 text-xs text-muted">
                  {t("home.last_update")}: {formatRelativeTime(gold.last_update)}
                </p>
                {gold.is_stale ? (
                  <p className="mt-1 text-xs text-warning">{t("market.stale_warning")}</p>
                ) : null}
              </>
            ) : goldError ? (
              <p className="text-sm text-warning">{goldError}</p>
            ) : (
              <p className="text-sm text-muted">{loading ? t("common.loading") : t("common.no_data")}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mt-4">
        <LatestDecisionsPanel decisions={assetDecisions} timeframe="5m" />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>{t("home.closed_trades")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl">{closedTrades}</p>
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

        <Card>
          <CardHeader><CardTitle>{t("home.today_activity")}</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {todayError ? (
              <p className="text-muted">{todayError}</p>
            ) : (
              <>
                <p>{t("home.market_checks_today")}: {today?.market_checks_today ?? 0}</p>
                <p>{t("home.entry_signals_today")}: {today?.entry_signals_today ?? 0}</p>
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
          <CardContent className="grid gap-3 sm:grid-cols-3 text-sm">
            {(["5m", "15m", "1h"] as const).map((timeframe) => {
              const runner = workers.workers.find((w) => w.name === "strategy_runner");
              const tf = runner?.timeframes?.[timeframe];
              return (
                <div key={timeframe} className="rounded-md bg-surface-elevated p-3">
                  <p className="font-medium">{timeframe}</p>
                  <p>
                    <span className="text-muted">{t("home.worker_backlog")}: </span>
                    {tf?.backlog ?? "—"}
                  </p>
                  <p>
                    <span className="text-muted">{t("home.worker_status_label")}: </span>
                    {tf?.status ?? runner?.status ?? "—"}
                  </p>
                </div>
              );
            })}
          </CardContent>
        </Card>
      ) : null}

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.quick_actions")}</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Link
            href="/portfolio-comparison"
            className="rounded-md bg-surface-elevated px-4 py-2 text-sm hover:bg-accent/20"
          >
            {t("home.competition_view")}
          </Link>
          <Link
            href="/decisions"
            className="rounded-md bg-surface-elevated px-4 py-2 text-sm hover:bg-accent/20"
          >
            {t("home.view_decisions")}
          </Link>
          <Link
            href="/backtests"
            className="rounded-md bg-surface-elevated px-4 py-2 text-sm hover:bg-accent/20"
          >
            {t("home.view_backtests")}
          </Link>
        </CardContent>
      </Card>
    </>
  );
}
