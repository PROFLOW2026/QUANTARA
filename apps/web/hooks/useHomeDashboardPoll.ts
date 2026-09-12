"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  isEngineConnectionError,
  type AssetAnalyticsResponse,
  type BrokerAccountSummary,
  type RiskConcentrationResponse,
  type Decision,
  type MarketProviderStatus,
  type TodayActivity,
  type WorkerStatus,
} from "@/lib/api-client";
import { loadCompetitionView } from "@/lib/competition-client";
import { t } from "@/lib/i18n";

const POLL_INTERVAL_MS = 60_000;

async function settle<T>(
  promise: Promise<T>
): Promise<{ ok: true; value: T } | { ok: false; error: unknown }> {
  try {
    return { ok: true, value: await promise };
  } catch (error) {
    return { ok: false, error };
  }
}

export function useHomeDashboardPoll() {
  const [assetDecisions, setAssetDecisions] = useState<Decision[]>([]);
  const [today, setToday] = useState<TodayActivity | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const [competition, setCompetition] = useState<Awaited<
    ReturnType<typeof loadCompetitionView>
  > | null>(null);
  const [assetAnalytics, setAssetAnalytics] = useState<AssetAnalyticsResponse | null>(
    null
  );
  const [marketStatus, setMarketStatus] = useState<MarketProviderStatus | null>(null);
  const [brokerAccount, setBrokerAccount] = useState<BrokerAccountSummary | null>(null);
  const [riskConcentration, setRiskConcentration] = useState<RiskConcentrationResponse | null>(
    null
  );
  const [engineHealthy, setEngineHealthy] = useState<boolean | null>(null);
  const [engineConnectionError, setEngineConnectionError] = useState(false);
  const [competitionUnavailable, setCompetitionUnavailable] = useState(false);
  const [todayError, setTodayError] = useState<string | null>(null);
  const [dataRefreshError, setDataRefreshError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const inFlightRef = useRef(false);
  const hadCompetitionRef = useRef(false);

  const fetchAll = useCallback(async (showLoading = false) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;

    if (showLoading) setLoading(true);

    let partialFailure = false;
    let healthDown = false;

    try {
      const healthResult = await settle(api.getEngineHealth());
      if (healthResult.ok) {
        setEngineHealthy(healthResult.value.status === "ok");
        setEngineConnectionError(false);
      } else {
        healthDown = isEngineConnectionError(healthResult.error);
        setEngineHealthy((prev) => (prev === null ? false : prev));
        setEngineConnectionError(healthDown);
        partialFailure = true;
      }

      const workersResult = await settle(api.getWorkersStatus());
      if (workersResult.ok) {
        setWorkers(workersResult.value);
      } else {
        partialFailure = true;
      }

      const analyticsResult = await settle(api.getAssetAnalytics());
      if (analyticsResult.ok) {
        setAssetAnalytics((prev) => {
          const next = analyticsResult.value;
          if (!next.summary) {
            return prev?.summary ? { ...next, summary: prev.summary } : next;
          }
          if (!prev?.summary) {
            return next;
          }
          const mergedSummary = { ...prev.summary, ...next.summary };
          if (next.summary.open_exposure == null && prev.summary.open_exposure != null) {
            mergedSummary.open_exposure = prev.summary.open_exposure;
          }
          if (next.summary.open_risk_usd == null && prev.summary.open_risk_usd != null) {
            mergedSummary.open_risk_usd = prev.summary.open_risk_usd;
          }
          if (next.summary.open_risk_pct == null && prev.summary.open_risk_pct != null) {
            mergedSummary.open_risk_pct = prev.summary.open_risk_pct;
          }
          return { ...next, summary: mergedSummary };
        });
      } else {
        partialFailure = true;
      }

      const marketResult = await settle(api.getMarketStatus());
      if (marketResult.ok) {
        setMarketStatus(marketResult.value);
      } else {
        partialFailure = true;
      }

      const brokerResult = await settle(api.getBrokerAccount());
      if (brokerResult.ok) {
        setBrokerAccount(brokerResult.value);
      } else {
        partialFailure = true;
      }

      const concentrationResult = await settle(api.getRiskConcentration());
      if (concentrationResult.ok) {
        setRiskConcentration(concentrationResult.value);
      } else {
        partialFailure = true;
      }

      if (showLoading) setLoading(false);

      const decisionsResult = await settle(api.getDecisionsByAsset("5m"));
      if (decisionsResult.ok) {
        setAssetDecisions(decisionsResult.value.decisions ?? []);
      } else {
        partialFailure = true;
      }

      const todayResult = await settle(api.getAnalyticsToday());
      if (todayResult.ok) {
        setToday(todayResult.value);
        setTodayError(null);
      } else {
        partialFailure = true;
        setTodayError((prev) => prev ?? t("common.section_unavailable"));
      }

      const competitionResult = await settle(loadCompetitionView());
      if (competitionResult.ok) {
        hadCompetitionRef.current = true;
        setCompetition(competitionResult.value);
        setCompetitionUnavailable(false);
      } else {
        partialFailure = true;
        setCompetitionUnavailable(!hadCompetitionRef.current);
      }

      setDataRefreshError(
        partialFailure && !healthDown ? t("home.data_refresh_stale") : null
      );
    } catch (e) {
      const down = isEngineConnectionError(e);
      setEngineConnectionError(down);
      setEngineHealthy((prev) => (prev === null ? false : prev));
      setDataRefreshError(down ? null : t("home.data_refresh_stale"));
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll(true);

    let intervalId: number | undefined;

    const startPolling = () => {
      if (intervalId !== undefined) return;
      intervalId = window.setInterval(() => {
        if (document.visibilityState === "visible") {
          void fetchAll(false);
        }
      }, POLL_INTERVAL_MS);
    };

    const stopPolling = () => {
      if (intervalId === undefined) return;
      window.clearInterval(intervalId);
      intervalId = undefined;
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void fetchAll(false);
        startPolling();
      } else {
        stopPolling();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") {
      startPolling();
    }

    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [fetchAll]);

  return {
    assetDecisions,
    today,
    workers,
    competition,
    assetAnalytics,
    brokerAccount,
    riskConcentration,
    marketStatus,
    engineHealthy,
    engineConnectionError,
    competitionUnavailable,
    todayError,
    dataRefreshError,
    loading,
    refresh: () => fetchAll(true),
  };
}
