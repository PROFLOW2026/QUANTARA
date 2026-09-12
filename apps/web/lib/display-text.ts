import { t } from "@/lib/i18n";
import { formatCurrency, formatPercent } from "@/lib/utils";

const STATUS_KEYS: Record<string, string> = {
  active: "display.status.active",
  running: "display.status.running",
  completed: "display.status.completed",
  failed: "display.status.failed",
  pending: "display.status.pending",
  draft: "display.status.draft",
  healthy: "display.status.healthy",
  conservation: "display.status.conservation",
  exhausted: "display.status.exhausted",
  halted: "display.status.halted",
  error: "display.status.failed",
};

const DECISION_TYPE_KEYS: Record<string, string> = {
  hold: "display.decision.hold",
  buy_signal: "display.decision.buy_signal",
  sell_signal: "display.decision.sell_signal",
  close_signal: "display.decision.close_signal",
  no_setup: "display.decision.no_setup",
  risk_denied: "display.decision.risk_denied",
  risk_approved: "display.decision.risk_approved",
  position_open: "display.decision.position_open",
  trading_halted: "display.decision.trading_halted",
  execution_failed: "display.decision.execution_failed",
  sl_triggered: "display.decision.sl_triggered",
  tp_triggered: "display.decision.tp_triggered",
  system_error: "display.decision.system_error",
};

const EXIT_REASON_KEYS: Record<string, string> = {
  sl: "display.exit.sl",
  tp: "display.exit.tp",
  strategy: "display.exit.strategy",
  manual: "display.exit.manual",
  risk_halt: "display.exit.risk_halt",
  end_of_backtest: "display.exit.end_of_backtest",
  system_exit: "display.exit.strategy",
};

const RISK_PROFILE_KEYS: Record<string, string> = {
  very_conservative: "display.risk.very_conservative",
  conservative: "display.risk.conservative",
  balanced: "display.risk.balanced",
  aggressive: "display.risk.aggressive",
  very_aggressive: "display.risk.very_aggressive",
};

const TIMEFRAME_KEYS: Record<string, string> = {
  "5m": "market.timeframe_5m",
  "15m": "market.timeframe_15m",
  "1h": "market.timeframe_1h",
};

const PARAM_LABEL_KEYS: Record<string, string> = {
  ema_fast: "strategies.param_ema_fast",
  ema_slow: "strategies.param_ema_slow",
  ema_trend: "strategies.param_ema_trend",
  rsi_period: "strategies.param_rsi_period",
  rsi_entry_min: "strategies.param_rsi_entry_min",
  rsi_entry_max: "strategies.param_rsi_entry_max",
  atr_period: "strategies.param_atr_period",
  atr_sl_multiplier: "strategies.param_atr_sl_multiplier",
  atr_tp_multiplier: "strategies.param_atr_tp_multiplier",
  min_candles_required: "strategies.param_min_candles",
};

export function translateStatus(status: string): string {
  const key = STATUS_KEYS[status.toLowerCase()];
  return key ? t(key) : status;
}

export function translateDecisionType(type: string): string {
  const normalized = type.toLowerCase().replace(/\s+/g, "_");
  const key = DECISION_TYPE_KEYS[normalized];
  if (key) return t(key);
  if (normalized.includes("buy")) return t("display.decision.buy_signal");
  if (normalized.includes("sell")) return t("display.decision.sell_signal");
  if (normalized.includes("hold") || normalized.includes("no_setup")) {
    return t("display.decision.no_setup");
  }
  return type;
}

export function translateExitReason(reason: string | null | undefined): string {
  if (!reason) return "—";
  const key = EXIT_REASON_KEYS[reason.toLowerCase()];
  return key ? t(key) : reason;
}

export function translateRiskProfile(profile: string | null | undefined): string {
  if (!profile) return "—";
  const key = RISK_PROFILE_KEYS[profile.toLowerCase()];
  return key ? t(key) : profile;
}

export function translateTimeframe(timeframe: string): string {
  return t(TIMEFRAME_KEYS[timeframe] ?? timeframe);
}

export function translateExecution(mode: string | null | undefined): string {
  if (!mode) return "—";
  if (mode === "next_open") return t("display.execution.next_open");
  return mode;
}

const ORB_REASON_KEYS: Record<string, string> = {
  waiting_for_breakout: "signals.orb_waiting_for_breakout",
  breakout_long_confirmed: "signals.orb_breakout_long_confirmed",
  breakout_short_confirmed: "signals.orb_breakout_short_confirmed",
  opening_range_building: "signals.orb_opening_range_building",
  opening_range_incomplete: "signals.orb_opening_range_incomplete",
  trade_already_taken_today: "signals.orb_trade_already_taken_today",
  entry_cutoff_passed: "signals.orb_entry_cutoff_passed",
  market_closed: "signals.orb_market_closed",
  breakout_already_consumed: "signals.orb_breakout_already_consumed",
};

const DATA_STATUS_KEYS: Record<string, string> = {
  healthy: "home.asset_status_healthy",
  fresh: "home.asset_status_fresh",
  deferred: "home.asset_status_deferred",
  closed: "home.asset_status_session_closed",
  stale: "home.asset_status_stale",
  blocked: "home.asset_status_blocked",
  error: "home.asset_status_error",
  unknown: "home.asset_status_unknown",
};

const PROVIDER_STATUS_KEYS: Record<string, string> = {
  healthy: "home.provider_status_healthy",
  error: "home.provider_status_temp_error",
  blocked: "home.provider_status_quota_blocked",
  conservation: "home.provider_status_conservation",
  exhausted: "home.provider_status_exhausted",
  unknown: "home.provider_status_unknown",
};

export function translateProviderStatus(status: string | null | undefined): string {
  const key = PROVIDER_STATUS_KEYS[(status ?? "unknown").toLowerCase()];
  return key ? t(key) : t("home.provider_status_unknown");
}

export function translateProviderError(
  lastError: string | null | undefined,
  provider: string,
  status?: string | null
): string {
  const normalizedStatus = (status ?? "").toLowerCase();
  if (normalizedStatus === "conservation") {
    return t("home.provider_status_conservation");
  }
  if (normalizedStatus === "exhausted") {
    return t("home.provider_status_exhausted");
  }
  if (!lastError) {
    return t("home.provider_error_unavailable");
  }

  const lower = lastError.toLowerCase();
  if (
    provider === "alpaca" &&
    (lower.includes("504") || lower.includes("timeout") || lower.includes("backend request timeout"))
  ) {
    return t("home.provider_error_temp");
  }
  if (
    provider === "twelvedata" &&
    (lower.includes("429") ||
      lower.includes("quota") ||
      lower.includes("credits") ||
      lower.includes("pricing") ||
      lower.includes("twelvedata.com"))
  ) {
    return t("home.provider_error_quota");
  }
  if (lower.includes("timeout") || lower.includes("504")) {
    return t("home.provider_error_temp");
  }
  if (lower.includes("429") || lower.includes("quota") || lower.includes("run out of api credits")) {
    return t("home.provider_error_quota");
  }
  return t("home.provider_error_unavailable");
}

export function formatProviderUsageLine(
  provider: string,
  health: {
    used_hour?: number;
    hourly_limit?: number | null;
    used_today?: number;
    guard_limit?: number;
    daily_limit?: number | null;
    used_day?: number;
  }
): string | null {
  if (provider === "tiingo") {
    const used = health.used_hour ?? 0;
    const limit = health.hourly_limit ?? 50;
    return t("home.provider_usage_hourly", { used, limit });
  }
  if (provider === "twelvedata") {
    const used = health.used_today ?? health.used_day ?? 0;
    const limit =
      (health as { provider_plan_limit?: number }).provider_plan_limit ??
      health.daily_limit ??
      800;
    return t("home.provider_usage_daily", { used, limit });
  }
  if (provider === "alpaca") {
    const usedDay = health.used_day ?? 0;
    const dayLimit = health.daily_limit;
    if (dayLimit != null) {
      return t("home.provider_usage_daily", { used: usedDay, limit: dayLimit });
    }
    const usedHour = health.used_hour ?? 0;
    if (usedHour > 0) {
      return t("home.provider_usage_hourly", { used: usedHour, limit: health.hourly_limit ?? "—" });
    }
  }
  return null;
}

export function providerHasTechnicalDetails(
  health: { last_error?: string | null; status?: string | null } | undefined
): boolean {
  if (!health) return false;
  return Boolean(health.last_error?.trim());
}

export function translateDataStatus(
  status: string | null | undefined,
  stale?: boolean,
  sessionClosed?: boolean
): string {
  if (sessionClosed && status !== "error" && status !== "blocked") {
    return t("home.asset_status_session_closed");
  }
  if (stale && status !== "error" && status !== "blocked") {
    return t("home.asset_status_stale");
  }
  const key = DATA_STATUS_KEYS[(status ?? "unknown").toLowerCase()];
  return key ? t(key) : status ?? "—";
}

export function translateRobotStrategyLabel(
  robotLabel?: string | null,
  strategyName?: string | null,
  strategySlug?: string | null
): string {
  if (robotLabel === "Robot A" || strategySlug === "gold-trend-pullback") {
    return t("home.robot_a_label");
  }
  if (robotLabel === "Robot B" || strategySlug === "opening-range-breakout") {
    return t("home.robot_b_label");
  }
  if (strategyName && robotLabel === "Robot A") return t("home.robot_a_label");
  if (strategyName && robotLabel === "Robot B") return t("home.robot_b_label");
  return robotLabel ?? strategyName ?? "—";
}

export function isEntrySignalDecision(decisionType: string | null | undefined): boolean {
  const normalized = (decisionType ?? "").toLowerCase();
  return normalized === "buy_signal" || normalized === "sell_signal";
}

export function translateSignalReason(reason: string | null | undefined): string {
  if (!reason) return "—";
  const text = reason.trim();

  const orbKey = ORB_REASON_KEYS[text];
  if (orbKey) return t(orbKey);

  if (text.startsWith("RISK_APPROVED:")) {
    const match = text.match(
      /qty=([^,]+).*target_risk=\$([0-9.]+)/
    );
    if (match) {
      return t("signals.risk_approved_summary", {
        qty: match[1],
        target: match[2],
      });
    }
    return t("display.decision.risk_approved");
  }

  if (text.startsWith("Pullback to EMA20 in uptrend")) {
    return t("signals.pullback_uptrend");
  }
  if (text.startsWith("Rally to EMA20 in downtrend")) {
    return t("signals.rally_downtrend");
  }
  if (text.startsWith("HOLD: trend valid")) {
    return t("signals.hold_uptrend");
  }
  if (text.startsWith("HOLD: downtrend valid")) {
    return t("signals.hold_downtrend");
  }
  if (text.startsWith("NO_SETUP: price below EMA200")) {
    return t("signals.no_long_below_trend");
  }
  if (text.startsWith("NO_SETUP: trend unclear")) {
    return t("signals.trend_unclear");
  }
  if (text.startsWith("TREND_REVERSAL: EMA50 crossed below EMA200")) {
    return t("signals.trend_reversal_bearish");
  }
  if (text.startsWith("TREND_REVERSAL: EMA50 crossed above EMA200")) {
    return t("signals.trend_reversal_bullish");
  }
  if (text.startsWith("INSUFFICIENT_DATA:")) {
    return t("signals.insufficient_data");
  }
  if (text.startsWith("NO_SETUP: RSI")) {
    const match = text.match(/RSI ([0-9.]+).*?\[([0-9]+)-([0-9]+)\]/);
    if (match) {
      return t("signals.rsi_out_of_range", {
        value: match[1],
        min: match[2],
        max: match[3],
      });
    }
    return t("signals.rsi_out_of_range_generic");
  }

  if (/^[a-z][a-z0-9_]+$/.test(text)) {
    return t("signals.internal_reason_unavailable");
  }

  return text;
}

export function formatStrategyParameterLines(
  parameters: Record<string, unknown> | null | undefined
): { label: string; value: string }[] {
  if (!parameters) return [];

  const formatValue = (key: string, value: unknown): string => {
    if (value == null) return "—";
    if (key.startsWith("ema_")) return `EMA ${value}`;
    if (key === "rsi_period") return `תקופה ${value}`;
    if (key === "atr_sl_multiplier" || key === "atr_tp_multiplier") return `ATR × ${value}`;
    if (key === "min_candles_required") return `${value} נרות`;
    return String(value);
  };

  const order = [
    "ema_fast",
    "ema_slow",
    "ema_trend",
    "rsi_period",
    "rsi_entry_min",
    "rsi_entry_max",
    "atr_period",
    "atr_sl_multiplier",
    "atr_tp_multiplier",
    "min_candles_required",
  ];

  const entries = Object.entries(parameters);
  const ordered = order
    .filter((key) => key in parameters)
    .map((key) => [key, parameters[key]] as const);
  const rest = entries.filter(([key]) => !order.includes(key));

  return [...ordered, ...rest].map(([key, value]) => ({
    label: t(PARAM_LABEL_KEYS[key] ?? key),
    value: formatValue(key, value),
  }));
}

export function shortenHash(hash: string | null | undefined, visible = 8): string {
  if (!hash) return "—";
  if (hash.length <= visible * 2 + 3) return hash;
  return `${hash.slice(0, visible)}…${hash.slice(-visible)}`;
}

export function insufficientMetric(value: number | null | undefined, minTrades = 3): boolean {
  return value == null || Number.isNaN(value);
}

export function formatCurrencyOrUnavailable(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return t("common.metric_unavailable");
  }
  return formatCurrency(value);
}

export function formatPercentOrUnavailable(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return t("common.metric_unavailable");
  }
  return formatPercent(value);
}

export function formatMetricOrInsufficient(
  value: number | null | undefined,
  formatter: (v: number) => string,
  tradeCount?: number | null
): string {
  if (tradeCount != null && tradeCount < 3) return t("analytics.insufficient_trades");
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) {
    return t("analytics.insufficient_trades");
  }
  return formatter(value);
}
