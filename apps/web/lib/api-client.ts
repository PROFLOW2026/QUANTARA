const ENGINE_URL =
  process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${ENGINE_URL}/api/v1${path.startsWith("/") ? path : `/${path}`}`;

  const headers: HeadersInit = {
    Accept: "application/json",
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
    ...options.headers,
  };

  const res = await fetch(url, {
    ...options,
    headers,
    cache: "no-store",
  });

  if (!res.ok) {
    throw new ApiError(res.status, `API ${res.status}: ${path}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

// --- Types (display-only, from Engine API) ---

export interface Portfolio {
  equity: number;
  cash_balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
  daily_pnl: number;
  peak_equity: number;
  current_drawdown_pct: number;
  risk_profile: string;
  mode: string;
}

export interface RiskStatus {
  profile: string;
  halted: boolean;
  exposure_pct: number;
  halt_reason?: string;
}

export interface Position {
  id: string;
  instrument: string;
  direction: "LONG" | "SHORT";
  size: number;
  entry_price: number;
  entry_time: string;
  current_price: number;
  stop_loss?: number;
  take_profit?: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  duration?: string;
  strategy_name?: string;
  strategy_version?: string;
}

export interface Trade {
  id: string;
  close_time: string;
  instrument: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  exit_price: number;
  pnl: number;
  duration?: string;
  exit_reason?: string;
  strategy_name?: string;
  strategy_version?: string;
  fees?: number;
}

export interface Decision {
  id: string;
  timestamp: string;
  decision_type: string;
  message: string;
  strategy_name?: string;
  instrument?: string;
  candle_time?: string;
  signal?: {
    direction?: string;
    entry_price?: number;
    stop_loss?: number;
    take_profit?: number;
    risk_target?: number;
    risk_actual?: number;
    execution?: string;
    reason?: string;
    strategy?: string;
    timeframe?: string;
  };
}

export interface Strategy {
  id: string;
  name: string;
  slug: string;
  status: string;
  versions_count: number;
  instruments: string[];
  timeframes: string[];
  active_instances: number;
}

export interface StrategyVersion {
  id: string;
  version: string;
  status: string;
  created_at: string;
  parameters?: Record<string, unknown>;
  trades_count: number;
  backtests_count: number;
  logic_hash?: string;
}

export interface BacktestMetrics {
  initial_capital?: number;
  final_capital?: number;
  total_return_pct?: number;
  total_trades?: number;
  winning_trades?: number;
  losing_trades?: number;
  win_rate?: number;
  average_win?: number;
  average_loss?: number;
  profit_factor?: number;
  maximum_drawdown_pct?: number;
  best_trade?: number;
  worst_trade?: number;
  expectancy?: number;
  equity_curve?: Array<{ date: string; equity: number }>;
  drawdown_curve?: Array<{ date: string; drawdown_pct: number }>;
}

export interface Backtest {
  id: string;
  name?: string;
  strategy_name: string;
  strategy_version: string;
  period_start: string;
  period_end: string;
  status: "pending" | "running" | "completed" | "failed";
  return_pct?: number;
  win_rate?: number;
  max_drawdown_pct?: number;
  trades_count?: number;
  created_at: string;
  progress?: { processed: number; total: number };
  profit_factor?: number;
  parameters?: Record<string, unknown>;
  execution_assumptions?: Record<string, unknown>;
  dataset_fingerprint?: string;
  metrics?: BacktestMetrics;
}

export interface Experiment {
  id: string;
  name: string;
  status: string;
  instances_count: number;
  period_start?: string;
  period_end?: string;
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface CandleLatest {
  instrument: string;
  price: number;
  change: number;
  change_pct: number;
  last_update: string;
  timeframe?: string;
}

export interface WorkerStatus {
  healthy: boolean;
  workers: Array<{
    name: string;
    status: "running" | "stopped" | "error";
    last_run?: string;
  }>;
}

export interface TodayActivity {
  scope: "paper";
  portfolio_id: string;
  strategy_instance_id: string | null;
  trades_count: number;
  decisions_count: number;
}

export interface PortfolioSnapshot {
  id: string;
  timestamp: string;
  equity: number;
  cash_balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
}

export interface AnalyticsPortfolio {
  equity_curve: Array<{ date: string; equity: number }>;
  drawdown_curve: Array<{ date: string; drawdown_pct: number }>;
  daily_returns: Array<{ date: string; return_pct: number }>;
  monthly_returns: Array<{ month: string; return_pct: number }>;
  summary: {
    total_return_pct: number;
    sharpe_ratio?: number;
    max_drawdown_pct: number;
    win_rate?: number;
  };
}

export interface AnalyticsStrategy {
  strategy_name: string;
  strategy_version: string;
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  long_pnl: number;
  short_pnl: number;
  avg_holding_time?: string;
  by_day_of_week?: Array<{ day: string; pnl: number }>;
  by_session?: Array<{ session: string; pnl: number }>;
}

export interface AnalyticsCosts {
  total_fees: number;
  total_slippage: number;
  spread_impact: number;
}

export interface Settings {
  timezone: string;
  default_risk_profile: string;
  paper_trading_enabled: boolean;
  initial_capital: number;
  trading_halted: boolean;
  execution_defaults: {
    spread: number;
    slippage: number;
    fees: number;
  };
  api_key_display?: string;
  language: string;
}

export interface MarketProviderStatus {
  healthy: boolean;
  provider?: string;
  source?: string;
  last_fetch?: string;
  candle_counts?: Record<string, number>;
  gaps?: number;
  stale: boolean;
}

// --- API methods ---

export const api = {
  getPortfolio: () => apiFetch<Portfolio>("/portfolio"),
  getRiskStatus: () => apiFetch<RiskStatus>("/portfolio/risk-status"),
  getSnapshots: () => apiFetch<PortfolioSnapshot[]>("/portfolio/snapshots"),
  getPositions: (status = "open") =>
    apiFetch<Position[]>(`/positions?status=${status}`),
  getTrades: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<Trade[]>(`/trades${qs}`);
  },
  getDecisions: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<Decision[]>(`/decisions${qs}`);
  },
  getLatestDecision: () => apiFetch<Decision>("/decisions/latest"),
  getStrategies: () => apiFetch<Strategy[]>("/strategies"),
  getStrategyVersions: (slug: string) =>
    apiFetch<StrategyVersion[]>(`/strategies/${slug}/versions`),
  getBacktests: () => apiFetch<Backtest[]>("/backtests"),
  getBacktest: (id: string) => apiFetch<Backtest>(`/backtests/${id}`),
  getBacktestTrades: (id: string) =>
    apiFetch<Trade[]>(`/backtests/${id}/trades`),
  getExperiments: () => apiFetch<Experiment[]>("/experiments"),
  getAnalyticsPortfolio: () =>
    apiFetch<AnalyticsPortfolio>("/analytics/portfolio"),
  getAnalyticsStrategy: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<AnalyticsStrategy>(`/analytics/strategy${qs}`);
  },
  getAnalyticsCosts: () => apiFetch<AnalyticsCosts>("/analytics/costs"),
  getAnalyticsToday: () => apiFetch<TodayActivity>("/analytics/today"),
  getCandlesLatest: (instrument = "XAU/USD") =>
    apiFetch<CandleLatest>(`/candles/latest?instrument=${encodeURIComponent(instrument)}`),
  getCandles: (params: Record<string, string>) => {
    const qs = new URLSearchParams(params).toString();
    return apiFetch<Candle[]>(`/candles?${qs}`);
  },
  getMarketStatus: () =>
    apiFetch<MarketProviderStatus>("/market-data/status"),
  getWorkersStatus: () => apiFetch<WorkerStatus>("/workers/status"),
  getSettings: () => apiFetch<Settings>("/settings"),
  updateSettings: (data: Partial<Settings>) =>
    apiFetch<Settings>("/settings", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  haltTrading: () =>
    apiFetch<void>("/paper/stop", { method: "POST" }),
  resumeTrading: () =>
    apiFetch<void>("/paper/start", { method: "POST" }),
};
