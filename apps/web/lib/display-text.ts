import { t } from "@/lib/i18n";

const STATUS_KEYS: Record<string, string> = {
  active: "display.status.active",
  running: "display.status.running",
  completed: "display.status.completed",
  failed: "display.status.failed",
  pending: "display.status.pending",
  draft: "display.status.draft",
  healthy: "display.status.healthy",
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

export function translateSignalReason(reason: string | null | undefined): string {
  if (!reason) return "—";
  const text = reason.trim();

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
