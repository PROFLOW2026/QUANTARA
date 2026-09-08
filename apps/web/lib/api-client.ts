import {
  ENGINE_PROXY_TIMEOUT_MS,
  resolveServerApiKey,
  resolveServerEngineUrl,
} from "./engine-server";

const BROWSER_PROXY_PREFIX = "/api/engine";

export const API_FETCH_TIMEOUT_MS = ENGINE_PROXY_TIMEOUT_MS;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isEngineConnectionError(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof ApiError) {
    return [408, 502, 503, 504, 499].includes(err.status) || err.status >= 500;
  }
  return false;
}

function buildFetchUrl(path: string): string {
  const enginePath = path.startsWith("/") ? path : `/${path}`;

  if (typeof window !== "undefined") {
    return `${BROWSER_PROXY_PREFIX}${enginePath}`;
  }

  return `${resolveServerEngineUrl()}/api/v1${enginePath}`;
}

function buildHeaders(options: RequestInit): HeadersInit {
  if (typeof window !== "undefined") {
    return {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    };
  }

  const apiKey = resolveServerApiKey();
  return {
    Accept: "application/json",
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
    ...options.headers,
  };
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = buildFetchUrl(path);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), API_FETCH_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      ...options,
      headers: buildHeaders(options),
      cache: "no-store",
      signal: controller.signal,
    });

    if (!res.ok) {
      let detail = `API ${res.status}: ${path}`;
      try {
        const payload = (await res.json()) as {
          error?: string;
          message?: string;
          detail?: string;
        };
        if (payload.message) detail = payload.message;
        else if (payload.error) detail = payload.error;
        else if (payload.detail) detail = payload.detail;
      } catch {
        // ignore JSON parse errors
      }
      throw new ApiError(res.status, detail);
    }

    if (res.status === 204) {
      return undefined as T;
    }

    return res.json() as Promise<T>;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(504, "upstream_timeout");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

// --- Types (display-only, from Engine API) ---

export interface Portfolio {
  id?: string;
  name?: string;
  equity: number;
  cash_balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
  daily_pnl: number;
  peak_equity: number;
  current_drawdown_pct: number;
  risk_profile: string;
  mode: string;
  initial_capital?: number;
  timeframe?: string;
  competition?: {
    timeframe: string;
    timeframe_he: string;
    risk_slug: string;
    risk_name_he: string;
    risk_per_trade_pct: number;
  };
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
  instrument_id?: string;
  candle_time?: string;
  timeframe?: string;
  fresh?: boolean;
  trade_opened?: boolean;
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
  price_source?: string;
  data_age_minutes?: number;
  is_stale?: boolean;
}

export interface WorkerTimeframeStatus {
  last_completed_candle?: string | null;
  last_processed_candle?: string | null;
  backlog?: number;
  status?: string;
}

export interface WorkerStatus {
  healthy: boolean;
  workers: Array<{
    name: string;
    status: "running" | "stopped" | "error";
    last_run?: string;
    execution_status?: string;
    backlog?: number;
    timeframes?: Record<string, WorkerTimeframeStatus>;
  }>;
}

export interface TodayActivity {
  scope: "paper" | "competition";
  portfolio_id?: string;
  strategy_instance_id?: string | null;
  trades_count?: number;
  decisions_count?: number;
  market_checks_today?: number;
  entry_signals_today?: number;
  trades_opened_today?: number;
  trades_closed_today?: number;
  sell_signals_today?: number;
  realized_pnl_today?: number;
  unrealized_pnl_total?: number;
  open_positions_total?: number;
  combined_equity?: number;
  leader?: {
    portfolio_id: string;
    name: string;
    return_pct: number;
    timeframe?: string;
    timeframe_he?: string;
  };
  leading_timeframe?: TimeframeComparisonRow | null;
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
  stale?: boolean;
  assets?: MarketAssetStatus[];
  providers?: Record<string, ProviderHealthStatus>;
  worker?: Record<string, unknown>;
  spot_source?: string;
  spot_age_minutes?: number | null;
}

export interface MarketAssetStatus {
  symbol: string;
  db_symbol: string;
  provider: string;
  secondary_provider?: string | null;
  status: string;
  last_candle?: string | null;
  latest_price?: number | null;
  session_status?: string;
  candle_counts?: Record<string, number>;
  stale?: boolean;
  timeframes_available?: Record<string, boolean>;
}

export interface ProviderHealthStatus {
  provider: string;
  status?: string;
  used_hour?: number;
  hourly_limit?: number | null;
  remaining_hour?: number | null;
  used_day?: number;
  daily_limit?: number | null;
  remaining_day?: number | null;
  active_symbols?: string[];
  last_success?: string | null;
  last_error?: string | null;
  used_today?: number;
  guard_limit?: number;
  remaining?: number;
}

export interface AssetAnalyticsRow {
  symbol: string;
  db_symbol: string;
  provider: string;
  latest_price?: number | null;
  last_candle?: string | null;
  data_status: string;
  stale: boolean;
  session_status: string;
  candle_counts: Record<string, number>;
  timeframes_available: Record<string, boolean>;
  strategy_ready: Record<string, boolean>;
  open_positions: number;
  closed_trades: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

export interface AssetAnalyticsResponse {
  assets_active: number;
  assets: AssetAnalyticsRow[];
}

export interface PortfolioListItem {
  id: string;
  name: string;
  kind: "competition";
  risk_slug?: string;
  risk_per_trade_pct?: number;
  timeframe?: string;
  timeframe_he?: string;
  sort_order?: number;
  initial_capital: number;
  balance?: number;
  equity: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  open_positions_count?: number;
  open_position?: boolean;
  open_direction?: string | null;
  closed_trades_count?: number;
}

export interface CompetitionLeaderboardRow {
  rank: number;
  portfolio_id: string;
  name: string;
  timeframe?: string;
  timeframe_he?: string;
  return_pct: number;
  max_drawdown_pct: number;
  realized_pnl: number;
  trades_count: number;
  win_rate?: number | null;
  return_vs_drawdown: number | null;
}

export interface TimeframeComparisonRow {
  timeframe: string;
  timeframe_he: string;
  title_he: string;
  portfolio_count: number;
  average_return_pct: number;
  best_return_pct: number;
  total_trades: number;
  closed_trades?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  total_pnl?: number;
  open_positions?: number;
  average_drawdown_pct: number;
  max_drawdown_pct: number;
  combined_equity: number;
}

export interface CompetitionTimeframeGroup {
  timeframe: string;
  timeframe_he: string;
  title_he: string;
  portfolios: CompetitionPortfolioSummary[];
}

export interface CompetitionPortfolioSummary {
  id: string;
  name: string;
  timeframe?: string;
  timeframe_he?: string;
  risk_slug: string;
  risk_name_he: string;
  risk_per_trade_pct: number;
  initial_capital: number;
  equity: number;
  balance: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  return_pct: number;
  trades_count: number;
  win_rate: number | null;
  max_drawdown_pct: number;
  exposure_pct: number;
  open_position: boolean;
  open_positions_count: number;
  status: string;
  strategy_instance_id: string;
  sort_order: number;
  target_risk_pct?: number;
  actual_risk_pct?: number | null;
  virtual_leverage?: number | null;
  notional_exposure?: number;
  open_direction?: string | null;
}

export interface CompetitionResponse {
  active: boolean;
  experiment?: {
    id: string;
    name: string;
    subtitle: string;
    started_at: string | null;
    status: string;
    strategy_name: string;
    strategy_version: string;
    instrument: string;
    timeframe: string;
    total_initial_capital: number;
    portfolio_initial_capital: number;
    portfolio_count: number;
  };
  combined?: {
    initial_equity: number;
    current_equity: number;
    combined_pnl: number;
    open_positions_total: number;
  };
  leader?: {
    portfolio_id: string;
    name: string;
    return_pct: number;
    timeframe?: string;
    timeframe_he?: string;
  };
  worst_performer?: {
    portfolio_id: string;
    name: string;
    return_pct: number;
    timeframe?: string;
    timeframe_he?: string;
  };
  leading_timeframe?: TimeframeComparisonRow | null;
  portfolios?: CompetitionPortfolioSummary[];
  timeframe_groups?: CompetitionTimeframeGroup[];
  leaderboard?: CompetitionLeaderboardRow[];
  leaderboards_by_timeframe?: Record<string, CompetitionLeaderboardRow[]>;
  timeframe_comparison?: TimeframeComparisonRow[];
  equity_curves?: Record<string, Array<{ date: string; equity: number }>>;
  activity?: Array<{
    timestamp: string;
    kind: string;
    message: string;
    portfolio_name?: string;
    timeframe?: string;
  }>;
  open_positions?: Array<{
    portfolio_id: string;
    portfolio_name: string;
    timeframe_he: string;
    direction: string;
    entry_price: number;
    current_price: number;
    stop_loss: number;
    take_profit: number | null;
    unrealized_pnl: number;
    quantity: number;
  }>;
  closed_trades?: Array<{
    trade_id: string;
    portfolio_name: string;
    timeframe_he: string;
    direction: string;
    entry_price: number;
    exit_price: number;
    quantity: number;
    realized_pnl: number;
    exit_reason: string;
    opened_at: string | null;
    closed_at: string | null;
  }>;
  today_summary?: {
    market_checks_today?: number;
    entry_signals_today?: number;
    sell_signals_today?: number;
    trades_opened_today?: number;
    trades_closed_today?: number;
    realized_pnl_today?: number;
    unrealized_pnl_total?: number;
  };
}

function portfolioQs(portfolioId?: string, extra?: Record<string, string>): string {
  const params = new URLSearchParams(extra);
  if (portfolioId) params.set("portfolio_id", portfolioId);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// --- API methods ---

export const api = {
  getPortfolio: (portfolioId?: string) =>
    apiFetch<Portfolio>(`/portfolio${portfolioQs(portfolioId)}`),
  getRiskStatus: (portfolioId?: string) =>
    apiFetch<RiskStatus>(`/portfolio/risk-status${portfolioQs(portfolioId)}`),
  getSnapshots: (portfolioId?: string) =>
    apiFetch<PortfolioSnapshot[]>(`/portfolio/snapshots${portfolioQs(portfolioId)}`),
  getPositions: (status = "open", portfolioId?: string) =>
    apiFetch<Position[]>(
      `/positions${portfolioQs(portfolioId, { status })}`
    ),
  getTrades: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<Trade[]>(`/trades${qs}`);
  },
  getDecisions: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<Decision[]>(`/decisions${qs}`);
  },
  getLatestDecision: () => apiFetch<Decision>("/decisions/latest"),
  getDecisionsByAsset: (timeframe = "5m") =>
    apiFetch<{ timeframe: string; decisions: Decision[] }>(
      `/decisions/by-asset?timeframe=${encodeURIComponent(timeframe)}`
    ),
  getStrategies: () => apiFetch<Strategy[]>("/strategies"),
  getStrategyVersions: (slug: string) =>
    apiFetch<StrategyVersion[]>(`/strategies/${slug}/versions`),
  getBacktests: () => apiFetch<Backtest[]>("/backtests"),
  getBacktest: (id: string) => apiFetch<Backtest>(`/backtests/${id}`),
  getBacktestTrades: (id: string) =>
    apiFetch<Trade[]>(`/backtests/${id}/trades`),
  getExperiments: () => apiFetch<Experiment[]>("/experiments"),
  getCompetition: () => apiFetch<CompetitionResponse>("/competition"),
  getPortfolios: () => apiFetch<PortfolioListItem[]>("/portfolios"),
  getAnalyticsPortfolio: (portfolioId?: string) =>
    apiFetch<AnalyticsPortfolio>(`/analytics/portfolio${portfolioQs(portfolioId)}`),
  getAnalyticsCompetition: () =>
    apiFetch<{
      equity_curves: Record<string, Array<{ date: string; equity: number }>>;
      portfolios: CompetitionPortfolioSummary[];
      leaderboard: CompetitionResponse["leaderboard"];
      combined: CompetitionResponse["combined"];
      experiment: CompetitionResponse["experiment"];
    }>("/analytics/competition"),
  getAnalyticsStrategy: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<AnalyticsStrategy>(`/analytics/strategy${qs}`);
  },
  getAnalyticsCosts: (portfolioId?: string) =>
    apiFetch<AnalyticsCosts>(`/analytics/costs${portfolioQs(portfolioId)}`),
  getAnalyticsToday: (portfolioId?: string) =>
    apiFetch<TodayActivity>(`/analytics/today${portfolioQs(portfolioId)}`),
  getCandlesLatest: (instrument = "XAU/USD") =>
    apiFetch<CandleLatest>(`/candles/latest?instrument=${encodeURIComponent(instrument)}`),
  getCandles: (params: Record<string, string>) => {
    const qs = new URLSearchParams(params).toString();
    return apiFetch<Candle[]>(`/candles?${qs}`);
  },
  getMarketStatus: () =>
    apiFetch<MarketProviderStatus>("/market-data/status"),
  getAssetAnalytics: () =>
    apiFetch<AssetAnalyticsResponse>("/analytics/assets"),
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
