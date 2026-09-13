"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  isEngineConnectionError,
  type AssetAnalyticsResponse,
  type BrokerAccountSummary,
  type LiveSimAccountSummary,
  type LiveSimCompareSummary,
  type RiskConcentrationResponse,
  type Decision,
  type MarketProviderStatus,
  type TodayActivity,
  type WorkerStatus,
} from "@/lib/api-client";
import { loadCompetitionView } from "@/lib/competition-client";
import { t } from "@/lib/i18n";

/** Tiered REST cadence when SSE live marks are connected (prices no longer need 30s poll). */
const FAST_INTERVAL_MS = 60_000;
const MEDIUM_INTERVAL_MS = 120_000;
const SLOW_INTERVAL_MS = 300_000;
/** Fallback when SSE is disconnected — restore prior safe REST cadence for prices. */
const SSE_FALLBACK_INTERVAL_MS = 30_000;

async function settle<T>(
  promise: Promise<T>
): Promise<{ ok: true; value: T } | { ok: false; error: unknown }> {
  try {
    return { ok: true, value: await promise };
  } catch (error) {
    return { ok: false, error };
  }
}

type PollOptions = {
  /** When true, prices come from direct Engine SSE; REST uses slower tiers. */
  liveStreamConnected?: boolean;
};

export function useHomeDashboardPoll(options: PollOptions = {}) {
  const { liveStreamConnected = false } = options;

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
  const [liveSimAccount, setLiveSimAccount] = useState<LiveSimAccountSummary | null>(null);
  const [liveSimCompare, setLiveSimCompare] = useState<LiveSimCompareSummary | null>(null);
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
  const lastMediumRef = useRef(0);
  const lastSlowRef = useRef(0);

  const mergeAnalyticsSummary = useCallback(
    (
      prev: AssetAnalyticsResponse | null,
      next: AssetAnalyticsResponse
    ): AssetAnalyticsResponse => {
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
    },
    []
  );

  const applyHealthResult = useCallback(
    (healthResult: { ok: true; value: Awaited<ReturnType<typeof api.getEngineHealth>> } | { ok: false; error: unknown }) => {
      if (healthResult.ok) {
        setEngineHealthy(healthResult.value.status === "ok");
        setEngineConnectionError(false);
        return false;
      }
      const healthDown = isEngineConnectionError(healthResult.error);
      setEngineHealthy((prev) => (prev === null ? false : prev));
      setEngineConnectionError(healthDown);
      return healthDown;
    },
    []
  );

  const fetchFast = useCallback(async () => {
    let partialFailure = false;
    const healthResult = await settle(api.getEngineHealth());
    const healthDown = applyHealthResult(healthResult);
    if (!healthResult.ok) {
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
      setAssetAnalytics((prev) => mergeAnalyticsSummary(prev, analyticsResult.value));
    } else {
      partialFailure = true;
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
    return partialFailure;
  }, [applyHealthResult, mergeAnalyticsSummary]);

  const fetchMedium = useCallback(async () => {
    let partialFailure = false;

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

    const liveSimResult = await settle(api.getLiveSimAccount());
    if (liveSimResult.ok) {
      setLiveSimAccount(liveSimResult.value);
    } else {
      partialFailure = true;
    }

    const concentrationResult = await settle(api.getRiskConcentration());
    if (concentrationResult.ok) {
      setRiskConcentration(concentrationResult.value);
    } else {
      partialFailure = true;
    }

    if (partialFailure) {
      setDataRefreshError((prev) => prev ?? t("home.data_refresh_stale"));
    }
  }, []);

  const fetchSlow = useCallback(async () => {
    let partialFailure = false;

    const compareResult = await settle(api.getLiveSimCompare());
    if (compareResult.ok) {
      setLiveSimCompare(compareResult.value);
    } else {
      partialFailure = true;
    }

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

    if (partialFailure) {
      setDataRefreshError((prev) => prev ?? t("home.data_refresh_stale"));
    }
  }, []);

  const fetchAll = useCallback(
    async (showLoading = false) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;

      if (showLoading) setLoading(true);

      try {
        await fetchFast();
        await fetchMedium();
        await fetchSlow();
        lastMediumRef.current = Date.now();
        lastSlowRef.current = Date.now();
      } catch (e) {
        const down = isEngineConnectionError(e);
        setEngineConnectionError(down);
        setEngineHealthy((prev) => (prev === null ? false : prev));
        setDataRefreshError(down ? null : t("home.data_refresh_stale"));
      } finally {
        inFlightRef.current = false;
        setLoading(false);
      }
    },
    [fetchFast, fetchMedium, fetchSlow]
  );

  const runTieredPoll = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      await fetchFast();
      const now = Date.now();
      if (now - lastMediumRef.current >= MEDIUM_INTERVAL_MS) {
        await fetchMedium();
        lastMediumRef.current = now;
      }
      if (now - lastSlowRef.current >= SLOW_INTERVAL_MS) {
        await fetchSlow();
        lastSlowRef.current = now;
      }
    } finally {
      inFlightRef.current = false;
    }
  }, [fetchFast, fetchMedium, fetchSlow]);

  const runFallbackPoll = useCallback(async () => {
    await fetchAll(false);
  }, [fetchAll]);

  useEffect(() => {
    void fetchAll(true);

    let intervalId: number | undefined;

    const pollIntervalMs = liveStreamConnected
      ? FAST_INTERVAL_MS
      : SSE_FALLBACK_INTERVAL_MS;

    const tick = () => {
      if (document.visibilityState !== "visible") {
        return;
      }
      if (liveStreamConnected) {
        void runTieredPoll();
      } else {
        void runFallbackPoll();
      }
    };

    const startPolling = () => {
      if (intervalId !== undefined) return;
      intervalId = window.setInterval(tick, pollIntervalMs);
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
  }, [fetchAll, liveStreamConnected, runFallbackPoll, runTieredPoll]);

  return {
    assetDecisions,
    today,
    workers,
    competition,
    assetAnalytics,
    brokerAccount,
    liveSimAccount,
    liveSimCompare,
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
