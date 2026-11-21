/**
 * QUANTARA Options — TypeScript mirror of
 * `apps/engine/quantara_options/contracts.py` and `api_schemas.py`.
 *
 * Pydantic v2 serializes `Decimal` fields as JSON strings, so every Decimal
 * field is typed as `DecimalValue` and must be read through `toNum()`.
 * Python `date` → ISO "YYYY-MM-DD", `datetime` → ISO-8601 string.
 */

export type DecimalValue = string | number;
export type IsoDate = string;
export type IsoDateTime = string;

// ── Enums ────────────────────────────────────────────────────────────────────

export type OptionRight = "call" | "put";
export type OptionSide = "long" | "short";
export type ExerciseStyle = "american" | "european";
export type DataFeedTier =
  | "alpaca_indicative"
  | "alpaca_opra"
  | "fixture"
  | "unavailable";
export type DecisionMode = "rules" | "ai";

export type StrategyType =
  | "long_call"
  | "long_put"
  | "covered_call"
  | "cash_secured_put"
  | "wheel"
  | "bull_call_spread"
  | "bear_put_spread"
  | "bull_put_credit_spread"
  | "bear_call_credit_spread"
  | "iron_condor";

export const STRATEGY_TYPES: StrategyType[] = [
  "long_call",
  "long_put",
  "covered_call",
  "cash_secured_put",
  "wheel",
  "bull_call_spread",
  "bear_put_spread",
  "bull_put_credit_spread",
  "bear_call_credit_spread",
  "iron_condor",
];

/** Mirrors `ALL_9_STRATEGY_LABELS_HE` in contracts.py. */
export const STRATEGY_LABELS_HE: Record<StrategyType, string> = {
  long_call: "Long CALL",
  long_put: "Long PUT",
  covered_call: "Covered Call",
  cash_secured_put: "Cash-Secured PUT",
  wheel: "Wheel Strategy",
  bull_call_spread: "Bull Call Spread",
  bear_put_spread: "Bear Put Spread",
  bull_put_credit_spread: "Bull Put Credit Spread",
  bear_call_credit_spread: "Bear Call Credit Spread",
  iron_condor: "Iron Condor",
};

export type StrategyStatus =
  | "open"
  | "closed"
  | "expired"
  | "assigned"
  | "rolled"
  | "rejected";

export type WheelState = "idle" | "short_put" | "holding_shares" | "short_call";

export type LegRole =
  | "long_call"
  | "short_call"
  | "long_put"
  | "short_put"
  | "underlying_stock";

export type AIAction = "OPEN" | "HOLD" | "CLOSE" | "ROLL" | "SKIP";

export type ItmStatus = "OTM" | "ATM_PIN_RISK" | "ITM";
export type ExpirationWarningLevel = "normal" | "watch" | "urgent";

export type JournalEventType =
  | "OPEN"
  | "CLOSE"
  | "ROLL"
  | "EXPIRE"
  | "ASSIGN"
  | "EXERCISE"
  | "STOCK_DELIVERY"
  | "FEE";

// ── contracts.py ─────────────────────────────────────────────────────────────

export interface Greeks {
  delta: number | null;
  gamma: number | null;
  /** per calendar day, $ per share */
  theta: number | null;
  /** per 1% vol change, $ per share */
  vega: number | null;
  rho: number | null;
  computed_locally: boolean;
}

export interface OptionContractSpec {
  occ_symbol: string;
  underlying_symbol: string;
  right: OptionRight;
  strike: DecimalValue;
  expiration_date: IsoDate;
  multiplier: number;
  exercise_style: ExerciseStyle;
  tradable: boolean;
  root_symbol: string | null;
  open_interest: number;
  close_price: DecimalValue | null;
}

export interface OptionQuoteSnapshot {
  contract: OptionContractSpec;
  underlying_price: DecimalValue;
  bid: DecimalValue | null;
  ask: DecimalValue | null;
  mid: DecimalValue | null;
  last_trade: DecimalValue | null;
  volume: number;
  open_interest: number;
  implied_volatility: number | null;
  greeks: Greeks;
  quote_timestamp: IsoDateTime | null;
  snapshot_timestamp: IsoDateTime;
  feed_tier: DataFeedTier;
  is_indicative: boolean;
  is_delayed_trades: boolean;
  is_stale: boolean;
  spread_pct: number | null;
  data_warnings: string[];
}

export interface OptionChainResponse {
  underlying_symbol: string;
  underlying_price: DecimalValue | null;
  as_of: IsoDateTime;
  market_open: boolean;
  feed_tier: DataFeedTier;
  is_indicative: boolean;
  is_delayed_trades: boolean;
  is_stale: boolean;
  expirations: IsoDate[];
  contracts: OptionQuoteSnapshot[];
  warnings: string[];
  provider_name: string;
}

export interface AccountRuleProfile {
  profile_name: string;
  margin_enabled: boolean;
  /** 1=CC/CSP, 2=+Long, 3=+Spreads/IC, 4=Naked */
  options_approval_level: number;
  enforce_pdt_rule: boolean;
  pdt_equity_threshold_usd: DecimalValue;
  max_risk_per_trade_pct: DecimalValue;
  max_collateral_pct: DecimalValue;
  max_open_strategies: number;
  max_daily_loss_pct: DecimalValue;
  max_drawdown_pct: DecimalValue;
  allow_earnings_short_premium: boolean;
  auto_close_itm_before_expiry_minutes: number;
  commission_per_contract_usd: DecimalValue;
  clearing_fee_per_contract_usd: DecimalValue;
  assignment_fee_usd: DecimalValue;
  base_slippage_of_half_spread: DecimalValue;
  allow_fractional_equity: boolean;
}

export interface LegPlan {
  /** null for the underlying stock leg */
  occ_symbol: string | null;
  underlying_symbol: string;
  leg_role: LegRole;
  side: OptionSide;
  right: OptionRight | null;
  strike: DecimalValue | null;
  expiration_date: IsoDate | null;
  multiplier: number;
  ratio: number;
  exercise_style: ExerciseStyle;
  bid: DecimalValue | null;
  ask: DecimalValue | null;
  mid: DecimalValue | null;
  greeks: Greeks;
  implied_volatility: number | null;
}

export interface StrategyCandidate {
  candidate_id: string;
  strategy_type: StrategyType;
  underlying_symbol: string;
  underlying_price: DecimalValue;
  contracts_qty: number;
  legs: LegPlan[];
  dte: number;
  expiration_date: IsoDate;
  is_credit: boolean;
  /** +credit or -debit per share */
  estimated_net_premium_per_unit: DecimalValue;
  /** multiplied by contract multiplier & qty */
  estimated_net_premium_total: DecimalValue;
  /** null = theoretically unlimited (Long Call) */
  max_profit_usd: DecimalValue | null;
  max_loss_usd: DecimalValue;
  collateral_required_usd: DecimalValue;
  break_even_prices: DecimalValue[];
  probability_of_profit: number | null;
  iv_rank: number | null;
  net_delta: number;
  net_theta: number;
  net_vega: number;
  liquidity_score: number;
  rules_score: number;
  feed_tier: DataFeedTier;
  is_indicative: boolean;
  warnings: string[];
  rationale_summary: string;
  skills_evidence: Record<string, unknown>;
}

export type DecidedBy =
  | "groq_openai_gpt_oss_120b"
  | "rules_engine"
  | "skills_rules_fallback"
  | (string & {});

export interface AIDecisionRecord {
  decision_id: string;
  account_slug: string;
  mode: DecisionMode;
  action: AIAction;
  strategy_type: StrategyType | null;
  candidate_id: string | null;
  position_group_id: string | null;
  underlying_symbol: string;
  contracts_qty: number;
  confidence: number;
  decided_by: DecidedBy;
  model_name: string | null;
  rationale_he: string;
  rationale_en: string;
  risk_guard_passed: boolean;
  risk_guard_reasons: string[];
  market_context: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface OptionAccountSummary {
  id: string;
  slug: string;
  name: string;
  mode: DecisionMode;
  /** null = multi-strategy, otherwise a single StrategyType slug */
  strategy_scope: string | null;
  starting_capital: number;
  cash_balance: number;
  cash_reserved: number;
  free_cash: number;
  margin_used: number;
  long_options_market_value: number;
  short_options_liability: number;
  stock_holdings_value: number;
  equity: number;
  premium_collected_gross: number;
  premium_realized_net: number;
  unrealized_pnl: number;
  realized_pnl: number;
  fees_paid: number;
  max_drawdown_usd: number;
  max_drawdown_pct: number;
  return_pct: number;
  open_strategies_count: number;
  closed_strategies_count: number;
  win_rate_pct: number | null;
  profit_factor: number | null;
  rule_profile: AccountRuleProfile;
  is_active: boolean;
  experiment_group: string | null;
  /** underlying symbol → WheelState */
  wheel_states: Record<string, WheelState | string>;
  created_at: IsoDateTime | null;
  updated_at: IsoDateTime | null;
}

export interface PayoffPoint {
  underlying_price: number;
  expiration_pnl: number;
  t0_pnl: number;
}

// ── api_schemas.py ───────────────────────────────────────────────────────────

export interface OptionsLegView {
  id: string;
  occ_symbol: string | null;
  underlying_symbol: string;
  leg_role: LegRole | string;
  side: OptionSide | string;
  right: OptionRight | string | null;
  strike: number | null;
  expiration_date: IsoDate | null;
  multiplier: number;
  quantity: number;
  entry_price: number;
  current_mark: number;
  bid: number | null;
  ask: number | null;
  unrealized_pnl: number;
  realized_pnl: number;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  implied_volatility: number | null;
  status: string;
}

export interface OptionsStrategyGroupView {
  id: string;
  account_slug: string;
  strategy_type: StrategyType | string;
  strategy_label_he: string;
  underlying_symbol: string;
  status: StrategyStatus | string;
  mode: DecisionMode;
  contracts_qty: number;
  expiration_date: IsoDate | null;
  dte: number | null;
  is_credit: boolean;
  /** +credit received or -debit paid at open */
  net_entry_cash_flow: number;
  /** cost to close short legs (always >= 0) */
  open_liability_usd: number;
  /** market value of long legs */
  long_value_usd: number;
  collateral_locked_usd: number;
  max_profit_usd: number | null;
  max_loss_usd: number;
  unrealized_pnl: number;
  realized_pnl: number;
  fees_paid: number;
  net_delta: number;
  net_theta: number;
  net_vega: number;
  pin_risk_warning: boolean;
  early_assignment_warning: boolean;
  wheel_state: WheelState | string | null;
  opened_at: IsoDateTime;
  closed_at: IsoDateTime | null;
  close_reason: string | null;
  ai_rationale: string | null;
  legs: OptionsLegView[];
  payoff_curve: PayoffPoint[];
}

export interface OptionsJournalEventView {
  id: string;
  account_slug: string;
  strategy_group_id: string | null;
  event_type: JournalEventType | string;
  underlying_symbol: string;
  strategy_type: StrategyType | string;
  cash_delta: number;
  realized_pnl_delta: number;
  collateral_delta: number;
  fee_usd: number;
  description: string;
  created_at: IsoDateTime;
  metadata: Record<string, unknown>;
}

export interface OptionsExpirationItem {
  strategy_group_id: string;
  account_slug: string;
  underlying_symbol: string;
  strategy_type: StrategyType | string;
  expiration_date: IsoDate;
  dte: number;
  strikes: number[];
  underlying_price: number | null;
  itm_status: ItmStatus | string;
  collateral_locked_usd: number;
  unrealized_pnl: number;
  warning_level: ExpirationWarningLevel | string;
}

export interface OptionsEngineStatusView {
  enabled: boolean;
  running_cycle: boolean;
  mode: DecisionMode;
  feed_tier: DataFeedTier;
  is_indicative: boolean;
  is_delayed_trades: boolean;
  market_open: boolean;
  data_freshness_seconds: number | null;
  last_cycle_at: IsoDateTime | null;
  last_error: string | null;
  groq_available: boolean;
  groq_model: string;
  groq_calls_today: number;
  readiness_technical: boolean;
  readiness_market_data: boolean;
  readiness_forward_sim: boolean;
  universe: string[];
}

export interface OptionsStrategyComparisonRow {
  cohort_key: string;
  label: string;
  account_slug: string;
  mode: DecisionMode;
  strategy_scope: string;
  starting_capital: number;
  equity: number;
  return_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
  premium_collected_gross: number;
  premium_realized_net: number;
  open_liability_usd: number;
  collateral_locked_usd: number;
  max_drawdown_pct: number;
  win_rate_pct: number | null;
  profit_factor: number | null;
  open_count: number;
  closed_count: number;
}

export interface OptionsLabOverviewResponse {
  engine_status: OptionsEngineStatusView;
  accounts: OptionAccountSummary[];
  selected_account: OptionAccountSummary | null;
  open_strategies: OptionsStrategyGroupView[];
  closed_strategies: OptionsStrategyGroupView[];
  opportunities: StrategyCandidate[];
  ai_decisions: AIDecisionRecord[];
  journal: OptionsJournalEventView[];
  expirations: OptionsExpirationItem[];
  comparison: OptionsStrategyComparisonRow[];
  chain_preview: OptionChainResponse | null;
}

export type OptionsControlAction = "start" | "stop" | "run_once";

export interface OptionsControlRequest {
  action: OptionsControlAction;
  use_fixtures?: boolean;
  symbols?: string[] | null;
  account_slug?: string | null;
}

export interface OptionsControlResponse {
  ok: boolean;
  engine_status: OptionsEngineStatusView;
  cycle_result?: unknown;
}

export interface OptionsSimulateOrderRequest {
  account_slug: string;
  candidate: StrategyCandidate;
}

// ── UI helpers ───────────────────────────────────────────────────────────────

export const OPTIONS_UNIVERSE = [
  "SPY",
  "QQQ",
  "IWM",
  "AAPL",
  "NVDA",
  "AMD",
  "PLTR",
  "MSFT",
] as const;

export type OptionsUniverseSymbol = (typeof OPTIONS_UNIVERSE)[number];

/** Coerce a Pydantic Decimal (serialized as string) or number to a JS number. */
export function toNum(value: DecimalValue | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function strategyLabel(type: string | null | undefined): string {
  if (!type) return "—";
  return STRATEGY_LABELS_HE[type as StrategyType] ?? type;
}
