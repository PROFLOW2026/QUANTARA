"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { Badge } from "@/components/ui/badge";
import { DecisionTypeBadge } from "@/components/trading/DecisionTypeBadge";
import {
  api,
  type AssetAnalyticsRow,
  type Candle,
  type CompetitionResponse,
  type Decision,
} from "@/lib/api-client";
import {
  resolveAssetDataStatusPresentation,
  translateRobotStrategyLabel,
  translateDecisionMessage,
  translateSignalReason,
  translateStructureRegime,
  translateTimeframe,
  translateVolatilityRegime,
} from "@/lib/display-text";
import { extractAssetFromPortfolioName } from "@/lib/portfolio-hierarchy";
import { t } from "@/lib/i18n";
import { cn, formatCurrency, formatRelativeTime } from "@/lib/utils";

const TIMEFRAMES = [
  { id: "5m", labelKey: "market.timeframe_5m" },
  { id: "15m", labelKey: "market.timeframe_15m" },
  { id: "1h", labelKey: "market.timeframe_1h" },
] as const;

const CHART_CANDLE_LIMIT = 120;

let competitionCache: CompetitionResponse | null = null;
let competitionPromise: Promise<CompetitionResponse> | null = null;

async function loadCompetitionDetail(): Promise<CompetitionResponse> {
  if (competitionCache) return competitionCache;
  if (!competitionPromise) {
    competitionPromise = api.getCompetition().then((payload) => {
      competitionCache = payload;
      return payload;
    });
  }
  return competitionPromise;
}

function providerLabel(name: string) {
  if (name === "twelvedata") return "Twelve Data";
  if (name === "tiingo") return "Tiingo";
  if (name === "alpaca") return "Alpaca";
  return name;
}

function decisionMatchesAsset(decision: Decision, asset: AssetAnalyticsRow): boolean {
  const dbSym = asset.db_symbol.toUpperCase().replace("/", "");
  const sym = asset.symbol.toUpperCase().replace("/", "");
  const inst = (decision.instrument ?? "").toUpperCase().replace("/", "");
  const instId = (decision.instrument_id ?? "").toUpperCase().replace("/", "");
  return inst === sym || inst === dbSym || instId === dbSym;
}

function directionLabel(direction?: string | null): string {
  const lower = (direction ?? "").toLowerCase();
  if (lower === "long") return t("common.long");
  if (lower === "short") return t("common.short");
  return direction ?? "—";
}

function statusBadge(
  status: string,
  stale?: boolean,
  sessionClosed?: boolean,
  hasLastCandle?: boolean
) {
  const { label, variant } = resolveAssetDataStatusPresentation(status, {
    stale,
    sessionClosed,
    hasLastCandle,
  });
  return <Badge variant={variant}>{label}</Badge>;
}

export function AssetChartModal({
  open,
  asset,
  decisions,
  onClose,
}: {
  open: boolean;
  asset: AssetAnalyticsRow | null;
  decisions: Decision[];
  onClose: () => void;
}) {
  const [timeframe, setTimeframe] = useState("15m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [chartHeight, setChartHeight] = useState(320);
  const [openPosition, setOpenPosition] = useState<
    NonNullable<CompetitionResponse["open_positions"]>[number] | null
  >(null);
  const candleCache = useRef(new Map<string, Candle[]>());

  const latestDecision = useMemo(() => {
    if (!asset) return null;
    const matches = decisions.filter((row) => decisionMatchesAsset(row, asset));
    if (!matches.length) return null;
    return [...matches].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    )[0];
  }, [asset, decisions]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const update = () => setChartHeight(mq.matches ? 420 : 280);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!open) {
      setTimeframe("15m");
      setCandles([]);
      setLoading(false);
      setError(false);
      setOpenPosition(null);
      candleCache.current.clear();
    }
  }, [open]);

  const fetchCandles = useCallback(async () => {
    if (!asset) return;
    const cacheKey = `${asset.db_symbol}:${timeframe}`;
    const cached = candleCache.current.get(cacheKey);
    if (cached) {
      setCandles(cached);
      setLoading(false);
      setError(false);
      return;
    }

    setLoading(true);
    setError(false);
    try {
      const rows = await api.getCandles({
        instrument_id: asset.db_symbol,
        timeframe,
        limit: String(CHART_CANDLE_LIMIT),
      });
      candleCache.current.set(cacheKey, rows);
      setCandles(rows);
    } catch {
      setCandles([]);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [asset, timeframe]);

  useEffect(() => {
    if (!open || !asset) return;
    void fetchCandles();
  }, [open, asset, fetchCandles]);

  useEffect(() => {
    if (!open || !asset || (asset.open_positions ?? 0) === 0) {
      setOpenPosition(null);
      return;
    }

    let cancelled = false;
    void loadCompetitionDetail()
      .then((detail) => {
        if (cancelled) return;
        const row = (detail.open_positions ?? []).find(
          (pos) => extractAssetFromPortfolioName(pos.portfolio_name) === asset.symbol
        );
        setOpenPosition(row ?? null);
      })
      .catch(() => {
        if (!cancelled) setOpenPosition(null);
      });

    return () => {
      cancelled = true;
    };
  }, [open, asset]);

  if (!open || !asset) return null;

  const sessionLabel = asset.session_closed
    ? t("home.asset_status_session_closed")
    : t(`home.session_${asset.session_status}`);

  return (
    <div className="fixed inset-0 z-[85] flex items-end justify-center p-2 sm:items-center sm:p-4">
      <button
        type="button"
        className="absolute inset-0 bg-overlay"
        aria-label={t("common.close_module")}
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="asset-chart-modal-title"
        dir="rtl"
        className="relative z-10 flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-lg border border-border bg-surface-elevated shadow-modal sm:max-h-[92vh]"
      >
        <div className="border-b border-border bg-modal-header px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 id="asset-chart-modal-title" className="truncate text-lg font-semibold">
                {asset.symbol}
              </h3>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                <span>{providerLabel(asset.provider)}</span>
                <span>
                  {t("market.current_price")}:{" "}
                  <span className="font-mono text-foreground">
                    {asset.latest_price != null ? formatCurrency(asset.latest_price) : "—"}
                  </span>
                </span>
                <span>{sessionLabel}</span>
                {statusBadge(
                  asset.data_status,
                  asset.stale,
                  asset.session_closed,
                  Boolean(asset.last_candle)
                )}
              </div>
            </div>
            <button
              type="button"
              className="shrink-0 rounded-md px-2 py-1 text-sm text-muted hover:bg-surface-inner hover:text-foreground"
              onClick={onClose}
            >
              {t("common.close_module")}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.id}
                type="button"
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs transition-colors",
                  timeframe === tf.id
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-surface-inner text-foreground-secondary hover:border-border-interactive hover:bg-surface-active hover:text-foreground"
                )}
                onClick={() => setTimeframe(tf.id)}
              >
                {t(tf.labelKey)}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-y-auto overflow-x-hidden p-4">
          <div className="min-w-0">
            {loading ? (
              <div
                className="flex items-center justify-center rounded-lg border border-border bg-surface bg-chart-plot"
                style={{ height: chartHeight }}
              >
                <p className="text-sm text-muted">{t("home.asset_chart_loading")}</p>
              </div>
            ) : error ? (
              <div
                className="flex items-center justify-center rounded-lg border border-border bg-surface bg-chart-plot"
                style={{ height: chartHeight }}
              >
                <p className="text-sm text-muted">{t("home.asset_chart_error")}</p>
              </div>
            ) : candles.length < 2 ? (
              <div
                className="flex items-center justify-center rounded-lg border border-border bg-surface bg-chart-plot"
                style={{ height: chartHeight }}
              >
                <p className="text-sm text-muted">{t("home.asset_chart_no_data")}</p>
              </div>
            ) : (
              <CandlestickChart candles={candles} height={chartHeight} timeframe={timeframe} />
            )}
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border border-border-nested bg-surface-inner p-3 text-sm">
              <p className="mb-2 font-medium">{t("home.market_regime_title")}</p>
              {asset.market_regime ? (
                <p className="text-xs leading-relaxed">
                  {translateStructureRegime(asset.market_regime.structure_regime)} ·{" "}
                  {translateVolatilityRegime(asset.market_regime.volatility_regime)}
                </p>
              ) : (
                <p className="text-xs text-muted">—</p>
              )}
            </div>

            <div className="rounded-md border border-border-nested bg-surface-inner p-3 text-sm">
              <p className="mb-2 font-medium">{t("home.asset_chart_latest_decision")}</p>
              {latestDecision ? (
                <div className="space-y-1 text-xs leading-relaxed">
                  <p>
                    {translateRobotStrategyLabel(
                      latestDecision.robot_label,
                      latestDecision.strategy_name,
                      latestDecision.strategy_slug
                    )}{" "}
                    · {translateTimeframe(latestDecision.timeframe ?? "5m")}
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <DecisionTypeBadge
                      type={latestDecision.decision_type}
                      metadata={latestDecision.metadata}
                    />
                    <span className="text-muted">{formatRelativeTime(latestDecision.timestamp)}</span>
                  </div>
                  <p className="whitespace-pre-wrap break-words">
                    {translateDecisionMessage(latestDecision)}
                  </p>
                </div>
              ) : (
                <p className="text-xs text-muted">—</p>
              )}
            </div>

            <div className="rounded-md border border-border-nested bg-surface-inner p-3 text-sm sm:col-span-2">
              <p className="mb-2 font-medium">{t("home.asset_chart_open_position")}</p>
              {openPosition ? (
                <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                  <p>
                    {t("competition.open_direction")}: {directionLabel(openPosition.direction)}
                  </p>
                  <p>
                    {t("competition.entry_price")}: {formatCurrency(openPosition.entry_price)}
                  </p>
                  <p>
                    {t("competition.stop_loss")}: {formatCurrency(openPosition.stop_loss)}
                  </p>
                  <p>
                    {t("competition.take_profit")}:{" "}
                    {openPosition.take_profit != null
                      ? formatCurrency(openPosition.take_profit)
                      : "—"}
                  </p>
                </div>
              ) : (
                <p className="text-xs text-muted">{t("home.asset_chart_no_open_position")}</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
