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
/** Never leave the Home shell in loading state longer than this on initial load. */
const INITIAL_LOAD_CAP_MS = 45_000;

async function settle<T>(
  promise: Promise<T>
): Promise<{ ok: true; value: T } | { ok: false; error: unknown }> {
  try {
    return { ok: true, value: await promise };
  } catch (error) {
    return { ok: false, error };
  }
}

function mergeAnalyticsResponse(
  prev: AssetAnalyticsResponse | null,
  next: AssetAnalyticsResponse
): AssetAnalyticsResponse {
  const merged: AssetAnalyticsResponse = { ...next };

  if (prev?.assets?.length && (!next.assets || next.assets.length === 0)) {
    merged.assets = prev.assets;
  }

  if (!next.summary) {
    merged.summary = prev?.summary;
    return merged;
  }
  if (!prev?.summary) {
    return merged;
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
  if (
    (next.summary.total_equity == null || next.summary.total_equity === 0) &&
    prev.summary.total_equity != null &&
    prev.summary.total_equity !== 0
  ) {
    mergedSummary.total_equity = prev.summary.total_equity;
  }
  merged.summary = mergedSummary;
  return merged;
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
  const [initialLoading, setInitialLoading] = useState(true);

  const inFlightRef = useRef(false);
  const hadCompetitionRef = useRef(false);
  const hasLoadedOnceRef = useRef(false);
  const lastMediumRef = useRef(0);
  const lastSlowRef = useRef(0);
  const liveStreamConnectedRef = useRef(liveStreamConnected);
  const fetchAllRef = useRef<(showLoading?: boolean) => Promise<void>>(async () => {});
  const runTieredPollRef = useRef<() => Promise<void>>(async () => {});
  const runFallbackPollRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    liveStreamConnectedRef.current = liveStreamConnected;
  }, [liveStreamConnected]);

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
      setAssetAnalytics((prev) => mergeAnalyticsResponse(prev, analyticsResult.value));
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
      if (!hadCompetitionRef.current) {
        setCompetitionUnavailable(true);
      }
    }

    setDataRefreshError(
      partialFailure && !healthDown ? t("home.data_refresh_stale") : null
    );
    return partialFailure;
  }, [applyHealthResult]);

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
      if (inFlightRef.current) {
        return;
      }
      inFlightRef.current = true;

      const useInitialLoading = showLoading && !hasLoadedOnceRef.current;
      if (useInitialLoading) {
        setInitialLoading(true);
      }

      try {
        await fetchFast();
        await fetchMedium();
        await fetchSlow();
        lastMediumRef.current = Date.now();
        lastSlowRef.current = Date.now();
        hasLoadedOnceRef.current = true;
      } catch (e) {
        const down = isEngineConnectionError(e);
        setEngineConnectionError(down);
        setEngineHealthy((prev) => (prev === null ? false : prev));
        setDataRefreshError(down ? null : t("home.data_refresh_stale"));
      } finally {
        inFlightRef.current = false;
        if (useInitialLoading || hasLoadedOnceRef.current) {
          setInitialLoading(false);
        }
      }
    },
    [fetchFast, fetchMedium, fetchSlow]
  );

  const runTieredPoll = useCallback(async () => {
    if (inFlightRef.current) {
      return;
    }
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
      hasLoadedOnceRef.current = true;
      setInitialLoading(false);
    } finally {
      inFlightRef.current = false;
    }
  }, [fetchFast, fetchMedium, fetchSlow]);

  const runFallbackPoll = useCallback(async () => {
    await fetchAll(false);
  }, [fetchAll]);

  fetchAllRef.current = fetchAll;
  runTieredPollRef.current = runTieredPoll;
  runFallbackPollRef.current = runFallbackPoll;

  useEffect(() => {
    const loadCapTimer = window.setTimeout(() => {
      setInitialLoading(false);
    }, INITIAL_LOAD_CAP_MS);

    void fetchAllRef.current(true).finally(() => {
      window.clearTimeout(loadCapTimer);
    });

    let pollTimerId: number | undefined;

    const stopPolling = () => {
      if (pollTimerId === undefined) {
        return;
      }
      window.clearTimeout(pollTimerId);
      pollTimerId = undefined;
    };

    const schedulePoll = () => {
      stopPolling();
      if (document.visibilityState !== "visible") {
        return;
      }
      const pollIntervalMs = liveStreamConnectedRef.current
        ? FAST_INTERVAL_MS
        : SSE_FALLBACK_INTERVAL_MS;
      pollTimerId = window.setTimeout(() => {
        pollTimerId = undefined;
        if (document.visibilityState === "visible") {
          if (liveStreamConnectedRef.current) {
            void runTieredPollRef.current();
          } else {
            void runFallbackPollRef.current();
          }
        }
        schedulePoll();
      }, pollIntervalMs);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void fetchAllRef.current(false);
        schedulePoll();
      } else {
        stopPolling();
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") {
      schedulePoll();
    }

    return () => {
      window.clearTimeout(loadCapTimer);
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

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
    loading: initialLoading,
    refresh: () => fetchAll(false),
  };
}
