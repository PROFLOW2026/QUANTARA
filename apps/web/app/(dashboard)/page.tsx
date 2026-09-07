"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader, ErrorBanner, WorkerIndicator } from "@/components/layout/PageHeader";
import { MetricCardCurrency } from "@/components/trading/MetricCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { PriceDisplay } from "@/components/trading/PriceDisplay";
import { SignalCard } from "@/components/trading/SignalCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  api,
  ApiError,
  type Portfolio,
  type RiskStatus,
  type Position,
  type Decision,
  type CandleLatest,
  type WorkerStatus,
  type TodayActivity,
} from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatPercent, formatRelativeTime } from "@/lib/utils";

const POLL_INTERVAL = 60_000;

export default function HomePageClient() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [gold, setGold] = useState<CandleLatest | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [today, setToday] = useState<TodayActivity | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        api.getPortfolio(),
        api.getCandlesLatest(),
        api.getLatestDecision(),
        api.getPositions("open"),
        api.getRiskStatus(),
        api.getAnalyticsToday(),
        api.getWorkersStatus(),
      ]);

      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length === results.length) {
        const reason = (failed[0] as PromiseRejectedResult).reason;
        throw reason instanceof ApiError ? reason : new Error(t("common.error"));
      }

      if (results[0].status === "fulfilled") setPortfolio(results[0].value);
      if (results[1].status === "fulfilled") setGold(results[1].value);
      if (results[2].status === "fulfilled") setDecision(results[2].value);
      if (results[3].status === "fulfilled") setPositions(results[3].value);
      if (results[4].status === "fulfilled") setRisk(results[4].value);
      if (results[5].status === "fulfilled") setToday(results[5].value);
      if (results[6].status === "fulfilled") setWorkers(results[6].value);

      setError(failed.length > 0 ? t("common.error") : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchAll]);

  const totalUnrealized = positions.reduce((s, p) => s + p.unrealized_pnl, 0);

  return (
    <>
      <PageHeader titleKey="home.title" />

      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCardCurrency
          label={t("home.equity")}
          value={portfolio?.equity ?? 0}
        />
        <Card>
          <CardHeader><CardTitle>{t("home.daily_pnl")}</CardTitle></CardHeader>
          <CardContent>
            {loading ? (
              <span className="text-muted">{t("common.loading")}</span>
            ) : (
              <PnLDisplay value={portfolio?.daily_pnl ?? 0} size="lg" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.drawdown")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono text-2xl text-loss">
              {formatPercent(-(portfolio?.current_drawdown_pct ?? 0))}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{t("home.worker_status")}</CardTitle></CardHeader>
          <CardContent>
            <WorkerIndicator healthy={workers?.healthy ?? false} />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
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
              </>
            ) : (
              <p className="text-sm text-muted">{loading ? t("common.loading") : t("common.no_data")}</p>
            )}
          </CardContent>
        </Card>

        <SignalCard decision={decision} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>{t("home.open_positions")}</CardTitle></CardHeader>
          <CardContent>
            <p className="text-2xl font-mono">{positions.length}</p>
            <div className="mt-2">
              <PnLDisplay value={totalUnrealized} size="sm" />
            </div>
            <Link href="/positions" className="mt-3 inline-block text-sm text-accent hover:underline">
              {t("common.view_all")} →
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("home.risk_status")}</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p><span className="text-muted">{t("home.profile")}: </span>{risk?.profile ?? "—"}</p>
            <p>
              <span className="text-muted">{t("common.status")}: </span>
              {risk?.halted ? t("common.halted") : t("common.active")}
            </p>
            <p><span className="text-muted">{t("home.exposure")}: </span>{risk?.exposure_pct ?? 0}%</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("home.today_activity")}</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>{t("home.trades_today")}: {today?.trades_count ?? 0}</p>
            <p>{t("home.decisions_today")}: {today?.decisions_count ?? 0}</p>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle>{t("home.quick_actions")}</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-3">
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
