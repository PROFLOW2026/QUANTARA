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
    return (
      [408, 502, 503, 504, 530, 499].includes(err.status) || err.status >= 500
    );
  }
  return false;
}

export type EngineHealthResponse = {
  status: string;
  service: string;
  api_version?: string;
};

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
  /** @deprecated Use entry_signal — means entry signal, not confirmed fill */
  trade_opened?: boolean;
  entry_signal?: boolean;
  position_open?: boolean;
  robot_label?: string;
  strategy_slug?: string;
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
  robot_label?: string | null;
  status: string;
  versions_count: number;
  instruments: string[];
  timeframes: string[];
  active_instances: number;
}

export interface OrbAssetStatus {
  market_open: boolean;
  market_closed: boolean;
  opening_range_high: number | null;
  opening_range_low: number | null;
  opening_range_size: number | null;
  range_ready: boolean;
  current_relation: string | null;
  trades_today: number;
  latest_signal: string | null;
  latest_reason: string | null;
  latest_signal_at: string | null;
}

export interface OrbStatusResponse {
  strategy_id: string;
  version: string;
  display_name: string;
  paper_enabled: boolean;
  portfolios_count: number;
  now_utc: string;
  assets: Record<string, OrbAssetStatus>;
}

export interface StrategyBreakdownItem {
  strategy_slug: string;
  strategy_name: string;
  strategy_version: string;
  robot_label?: string;
  trade_count: number;
  configured?: boolean;
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  long_pnl: number;
  short_pnl: number;
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

export interface OrbWeekdayRow {
  weekday: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  average_R: number | null;
  expectancy: number;
  profit_factor: number | null;
}

export interface OrbRangeWidthRow {
  range_bucket: string;
  width_pct_min: number;
  width_pct_max: number | null;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  average_R: number | null;
  expectancy: number;
  profit_factor: number | null;
}

export interface OrbBacktestAnalytics {
  weekday_breakdown: OrbWeekdayRow[];
  range_width_breakdown: OrbRangeWidthRow[];
  range_width_buckets: Array<{ label: string; width_pct_min: number; width_pct_max: number | null }>;
  trade_details: Array<Record<string, unknown>>;
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
  orb_analytics?: OrbBacktestAnalytics;
}

export interface Backtest {
  id: string;
  name?: string;
  strategy_name: string;
  strategy_slug?: string;
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

export interface StrategyFreshness {
  healthy: boolean;
  stalled: boolean;
  status?: string;
  error?: string | null;
  mode?: string;
  last_evaluation_at?: string | null;
  evaluation_age_minutes?: number | null;
  backlog?: number;
  live_backlog?: number;
  historical_backlog?: number;
  market_candle_age_minutes?: Record<string, number | null>;
  fetch_status?: string;
}

export interface WorkerStatus {
  healthy: boolean;
  strategy_freshness?: StrategyFreshness;
  workers: Array<{
    name: string;
    status: "running" | "stopped" | "error";
    last_run?: string;
    execution_status?: string;
    backlog?: number;
    timeframes?: Record<string, WorkerTimeframeStatus>;
    freshness?: StrategyFreshness;
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
  strategy_signals_today?: number;
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

export type TradingControlState =
  | "running"
  | "pause_new_entries"
  | "pause_trading"
  | "flattening"
  | "stopped";

export interface TradingControlStatus {
  state: TradingControlState;
  updated_at?: string | null;
  flatten_started_at?: string | null;
  open_positions_at_flatten?: number;
  open_positions_remaining?: number;
  flatten_progress_pct?: number | null;
  assets_awaiting_market_reopen?: string[];
  pending_market_reopen?: string[];
}

export interface Settings {
  timezone: string;
  default_risk_profile: string;
  paper_trading_enabled: boolean;
  initial_capital: number;
  trading_halted: boolean;
  trading_control?: TradingControlStatus;
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
  provider_plan_limit?: number;
  guard_limit?: number;
  internal_guard_limit?: number;
  internal_guard_active?: boolean;
  remaining?: number;
  usable_budget?: number;
  candle_remaining?: number;
  fx_reserve?: number;
  budget_mode?: string;
  fallback_mode?: boolean;
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
  session_closed?: boolean;
  candle_counts: Record<string, number>;
  timeframes_available: Record<string, boolean>;
  strategy_ready: Record<string, boolean>;
  open_positions: number;
  closed_trades: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  open_exposure?: number | null;
  open_risk_usd?: number | null;
  open_risk_pct?: number | null;
  global_risk_cap_pct?: number;
  market_regime?: {
    structure_regime?: string;
    volatility_regime?: string;
    candle_time?: string;
  } | null;
}

export interface RiskConcentrationSymbolRow {
  symbol: string;
  gross_exposure_usd: number;
  net_exposure_usd: number;
  long_exposure_usd: number;
  short_exposure_usd: number;
  sl_risk_usd: number;
  sl_risk_pct: number;
  portfolio_count: number;
  robot_count: number;
  robots: string[];
}

export interface RiskConcentrationGroupRow {
  group: string;
  gross_exposure_usd: number;
  net_exposure_usd: number;
  sl_risk_usd: number;
  sl_risk_pct: number;
  assets: string[];
  robot_count: number;
}

export interface RegimePerformanceRow {
  robot_label: string;
  structure_regime: string;
  volatility_regime: string;
  risk_slug: string;
  trades: number;
  win_rate: number;
  realized_pnl: number;
  fees: number;
  long_trades: number;
  short_trades: number;
  average_r: number | null;
  average_trade: number;
}

export interface RiskConcentrationResponse {
  mode: string;
  broker_equity_usd: number;
  symbols: RiskConcentrationSymbolRow[];
  groups: RiskConcentrationGroupRow[];
}

export interface AssetAnalyticsSummary {
  open_exposure: number | null;
  open_risk_usd: number | null;
  open_risk_pct: number | null;
  total_equity: number;
  open_position_count?: number;
  risk_found_count?: number;
  risk_missing_count?: number;
  risk_zero_valid_count?: number;
  exposure_available?: boolean;
  remaining_sl_risk_usd?: number | null;
  projected_equity_at_stops?: number | null;
}

export interface AssetAnalyticsResponse {
  assets_active: number;
  summary?: AssetAnalyticsSummary;
  assets: AssetAnalyticsRow[];
}

export interface PortfolioListItem {
  id: string;
  name: string;
  kind: "competition";
  robot_label?: string;
  strategy_slug?: string;
  strategy_name?: string;
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

export interface RobotGroupSummary {
  robot_label: string;
  strategy_name: string;
  strategy_slug: string;
  experiment_id: string;
  portfolio_count: number;
  initial_capital: number;
  current_equity: number;
  combined_pnl: number;
}

export interface CompetitionPortfolioSummary {
  id: string;
  name: string;
  robot_label?: string;
  strategy_slug?: string;
  strategy_name?: string;
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
    shadow_reference_capital?: number;
    physical_broker_capital?: number;
    robot_a_portfolio_count?: number;
    robot_b_portfolio_count?: number;
    robot_c_portfolio_count?: number;
    robot_d_portfolio_count?: number;
    robot_e_portfolio_count?: number;
    robot_a_initial_capital?: number;
    robot_b_initial_capital?: number;
    robot_cde_initial_capital?: number;
    multi_strategy_started_at?: string | null;
  };
  robot_groups?: RobotGroupSummary[];
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
    robot_label?: string;
    strategy_slug?: string;
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
  strategy_signals_today?: number;
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

export interface BrokerAccountSummary {
  profile: string;
  account_state: string;
  cash: number;
  balance: number;
  equity: number;
  realized_pnl: number;
  gross_realized_pnl?: number;
  fees_paid?: number;
  net_realized_pnl?: number;
  unrealized_pnl: number;
  available_margin: number;
  spot_crypto_cash?: number;
  buying_power: number;
  position_mode?: string;
  hedging_supported?: boolean;
  initial_margin_used: number;
  maintenance_margin_required: number;
  free_margin: number;
  margin_level_pct: number | null;
  gross_exposure: number;
  net_exposure: number;
  gross_leverage: number;
  net_leverage: number;
  broker_positions: Array<{
    symbol: string;
    net_quantity: number;
    average_price: number;
    mark_price: number;
    unrealized_pnl: number;
  }>;
  physical_remaining_sl_risk_usd?: number | null;
  physical_risk_complete?: boolean;
  physical_risk_missing_count?: number;
  projected_broker_equity_at_stops?: number | null;
}

export interface BrokerRejection {
  timestamp: string;
  symbol: string;
  quantity: number;
  reason: string;
  detail?: string | null;
  opportunity_key?: string | null;
  portfolio_id?: string | null;
}

export interface LiveSimAllocation {
  id: string;
  strategy_slug: string;
  robot_label?: string | null;
  symbol: string;
  timeframe: string;
  direction: string;
  signal_candle_timestamp: string;
  proposed_entry?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  calculated_risk_usd?: number | null;
  calculated_quantity?: number | null;
  accepted: boolean;
  rejection_reason?: string | null;
  rejection_reason_he?: string;
  rejection_detail?: string | null;
  resulting_open_sl_risk_usd?: number | null;
  created_at: string;
}

export interface LiveSimAccountSummary {
  available: boolean;
  slug?: string;
  label_he?: string;
  starting_capital?: number;
  equity?: number;
  cash?: number;
  balance?: number;
  available_margin?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  daily_pnl?: number;
  total_return_pct?: number;
  high_water_mark?: number;
  current_drawdown_pct?: number;
  max_drawdown_pct?: number;
  open_sl_risk_usd?: number;
  open_sl_risk_pct?: number;
  gross_exposure?: number;
  net_exposure?: number;
  fees_paid?: number;
  started_at?: string | null;
  runtime_duration_he?: string | null;
  closed_trades_count?: number;
  win_rate_pct?: number;
  open_positions?: Array<{
    id: string;
    symbol: string;
    direction: string;
    robot?: string | null;
    strategy_slug: string;
    timeframe: string;
    quantity: number;
    entry_price: number;
    current_price: number;
    stop_loss: number;
    take_profit?: number | null;
    planned_sl_risk_usd: number;
    unrealized_pnl: number;
    opened_at?: string | null;
  }>;
  candidates?: {
    total: number;
    accepted: number;
    rejected: number;
    acceptance_rate_pct: number;
  };
  recent_decisions?: LiveSimAllocation[];
  risk_settings?: {
    risk_per_trade_pct: number;
    max_total_open_sl_risk_pct: number;
    max_symbol_sl_risk_pct: number;
    max_group_sl_risk_pct: number;
    daily_loss_gate_pct: number;
    max_drawdown_gate_pct: number;
    concentration_mode: string;
    risk_per_trade_usd_approx: number;
  };
}

export interface LiveSimCompareSummary {
  available: boolean;
  research?: {
    label_he: string;
    return_pct: number;
    current_drawdown_pct: number;
    max_drawdown_pct: number;
    win_rate_pct: number;
    closed_trades: number;
    open_positions: number;
    sl_risk_pct: number;
    gross_exposure_pct: number;
    realized_pnl: number;
    unrealized_pnl: number;
    fees_paid: number;
    equity: number;
    starting_capital: number;
  };
  live_sim?: {
    label_he: string;
    return_pct: number;
    current_drawdown_pct: number;
    max_drawdown_pct: number;
    win_rate_pct: number;
    closed_trades: number;
    open_positions: number;
    sl_risk_pct: number;
    gross_exposure_pct: number;
    realized_pnl: number;
    unrealized_pnl: number;
    fees_paid: number;
    equity: number;
    starting_capital: number;
    candidates_total?: number;
    candidates_accepted?: number;
    candidates_rejected?: number;
    acceptance_rate_pct?: number;
  };
}

export const api = {
  getBrokerAccount: () => apiFetch<BrokerAccountSummary>("/broker/account"),
  getLiveSimAccount: () => apiFetch<LiveSimAccountSummary>("/live-sim/account"),
  getLiveSimCompare: () => apiFetch<LiveSimCompareSummary>("/live-sim/compare"),
  getBrokerRejections: (limit = 100) =>
    apiFetch<{ available: boolean; rejections: BrokerRejection[] }>(
      `/broker/rejections?limit=${limit}`
    ),
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
  getDecisionsByAsset: (timeframe = "5m", includeFreshness = false) =>
    apiFetch<{ timeframe: string; decisions: Decision[]; strategy_freshness?: StrategyFreshness }>(
      `/decisions/by-asset?timeframe=${encodeURIComponent(timeframe)}&include_freshness=${includeFreshness ? "true" : "false"}`
    ),
  getStrategies: () => apiFetch<Strategy[]>("/strategies"),
  getStrategyVersions: (slug: string) =>
    apiFetch<StrategyVersion[]>(`/strategies/${slug}/versions`),
  getOrbStatus: (symbol?: string) => {
    const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    return apiFetch<OrbStatusResponse>(`/strategies/opening-range-breakout/status${qs}`);
  },
  getBacktests: () => apiFetch<Backtest[]>("/backtests"),
  getBacktest: (id: string) => apiFetch<Backtest>(`/backtests/${id}`),
  getBacktestTrades: (id: string) =>
    apiFetch<Trade[]>(`/backtests/${id}/trades`),
  getExperiments: () => apiFetch<Experiment[]>("/experiments"),
  getCompetition: () => apiFetch<CompetitionResponse>("/competition"),
  getCompetitionEquityCurves: () =>
    apiFetch<{ equity_curves: Record<string, { date: string; equity: number }[]> }>(
      "/competition/equity-curves"
    ),
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
      robot_groups: RobotGroupSummary[];
    }>("/analytics/competition"),
  getAnalyticsStrategy: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return apiFetch<AnalyticsStrategy>(`/analytics/strategy${qs}`);
  },
  getAnalyticsStrategyBreakdown: () =>
    apiFetch<{ strategies: StrategyBreakdownItem[] }>("/analytics/strategy-breakdown"),
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
  getRiskConcentration: () =>
    apiFetch<RiskConcentrationResponse>("/analytics/risk-concentration"),
  getRegimePerformance: () =>
    apiFetch<{ rows: RegimePerformanceRow[]; trade_count: number }>(
      "/analytics/regime-performance"
    ),
  getEngineHealth: () => apiFetch<EngineHealthResponse>("/health"),
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
  getTradingControl: () => apiFetch<TradingControlStatus>("/trading-control"),
  setTradingControl: (action: string) =>
    apiFetch<TradingControlStatus>(`/trading-control/${action}`, { method: "POST" }),
};
