/**
 * QUANTARA Options Lab — Drizzle definitions mirroring
 * packages/db/migrations/0025_quantara_options_lab.sql.
 *
 * Isolated on purpose: NOT re-exported from ./index so existing equity schema
 * consumers are unaffected. Import directly from "@quantara/db/schema/options".
 */
import {
  boolean,
  date,
  doublePrecision,
  index,
  integer,
  jsonb,
  numeric,
  pgTable,
  text,
  timestamp,
  unique,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";

const ts = (name: string) => timestamp(name, { withTimezone: true, mode: "string" });
const usd = (name: string) => numeric(name, { precision: 18, scale: 2 });
const px = (name: string) => numeric(name, { precision: 18, scale: 4 });

export const optAccounts = pgTable("opt_accounts", {
  id: uuid("id").primaryKey().defaultRandom(),
  slug: varchar("slug", { length: 64 }).notNull().unique(),
  name: varchar("name", { length: 128 }).notNull(),
  mode: varchar("mode", { length: 16 }).notNull().default("ai"), // 'rules' | 'ai'
  strategyScope: varchar("strategy_scope", { length: 64 }),
  experimentGroup: varchar("experiment_group", { length: 64 }).notNull().default("options_lab"),
  startingCapital: usd("starting_capital").notNull().default("0"),
  cashBalance: usd("cash_balance").notNull().default("0"),
  cashReserved: usd("cash_reserved").notNull().default("0"),
  marginUsed: usd("margin_used").notNull().default("0"),
  longOptionsMarketValue: usd("long_options_market_value").notNull().default("0"),
  shortOptionsLiability: usd("short_options_liability").notNull().default("0"),
  stockHoldingsValue: usd("stock_holdings_value").notNull().default("0"),
  equity: usd("equity").notNull().default("0"),
  peakEquity: usd("peak_equity").notNull().default("0"),
  maxDrawdownUsd: usd("max_drawdown_usd").notNull().default("0"),
  maxDrawdownPct: numeric("max_drawdown_pct", { precision: 8, scale: 4 }).notNull().default("0"),
  premiumCollectedGross: usd("premium_collected_gross").notNull().default("0"),
  premiumRealizedNet: usd("premium_realized_net").notNull().default("0"),
  unrealizedPnl: usd("unrealized_pnl").notNull().default("0"),
  realizedPnl: usd("realized_pnl").notNull().default("0"),
  feesPaid: usd("fees_paid").notNull().default("0"),
  ruleProfile: jsonb("rule_profile").notNull().default({}),
  wheelStates: jsonb("wheel_states").notNull().default({}),
  runtimeMeta: jsonb("runtime_meta").notNull().default({}),
  isActive: boolean("is_active").notNull().default(true),
  createdAt: ts("created_at").notNull().defaultNow(),
  updatedAt: ts("updated_at").notNull().defaultNow(),
});

export const optContracts = pgTable(
  "opt_contracts",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    occSymbol: varchar("occ_symbol", { length: 32 }).notNull().unique(),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    optionRight: varchar("option_right", { length: 8 }).notNull(), // 'call' | 'put'
    strike: px("strike").notNull(),
    expirationDate: date("expiration_date", { mode: "string" }).notNull(),
    multiplier: integer("multiplier").notNull().default(100),
    exerciseStyle: varchar("exercise_style", { length: 16 }).notNull().default("american"),
    rootSymbol: varchar("root_symbol", { length: 16 }),
    tradable: boolean("tradable").notNull().default(true),
    openInterest: integer("open_interest").notNull().default(0),
    closePrice: px("close_price"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [index("idx_opt_contracts_underlying_exp").on(t.underlyingSymbol, t.expirationDate)],
);

export const optChainSnapshots = pgTable(
  "opt_chain_snapshots",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    occSymbol: varchar("occ_symbol", { length: 32 }).notNull(),
    underlyingPrice: px("underlying_price"),
    bid: px("bid"),
    ask: px("ask"),
    mid: px("mid"),
    lastTrade: px("last_trade"),
    volume: integer("volume").notNull().default(0),
    openInterest: integer("open_interest").notNull().default(0),
    impliedVolatility: doublePrecision("implied_volatility"),
    delta: doublePrecision("delta"),
    gamma: doublePrecision("gamma"),
    theta: doublePrecision("theta"),
    vega: doublePrecision("vega"),
    rho: doublePrecision("rho"),
    greeksComputedLocally: boolean("greeks_computed_locally").notNull().default(false),
    feedTier: varchar("feed_tier", { length: 32 }).notNull().default("alpaca_indicative"),
    isIndicative: boolean("is_indicative").notNull().default(true),
    isDelayedTrades: boolean("is_delayed_trades").notNull().default(true),
    isStale: boolean("is_stale").notNull().default(false),
    spreadPct: doublePrecision("spread_pct"),
    quoteTimestamp: ts("quote_timestamp"),
    snapshotTimestamp: ts("snapshot_timestamp").notNull().defaultNow(),
    dataWarnings: jsonb("data_warnings").notNull().default([]),
  },
  (t) => [
    index("idx_opt_chain_snap_underlying_ts").on(t.underlyingSymbol, t.snapshotTimestamp.desc()),
    index("idx_opt_chain_snap_occ_ts").on(t.occSymbol, t.snapshotTimestamp.desc()),
  ],
);

export const optStrategies = pgTable(
  "opt_strategies",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => optAccounts.id),
    accountSlug: varchar("account_slug", { length: 64 }).notNull(),
    strategyType: varchar("strategy_type", { length: 64 }).notNull(),
    strategyLabelHe: varchar("strategy_label_he", { length: 128 }).notNull().default(""),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    status: varchar("status", { length: 24 }).notNull().default("open"),
    mode: varchar("mode", { length: 16 }).notNull().default("ai"),
    contractsQty: integer("contracts_qty").notNull().default(1),
    expirationDate: date("expiration_date", { mode: "string" }),
    isCredit: boolean("is_credit").notNull().default(false),
    netEntryCashFlow: usd("net_entry_cash_flow").notNull().default("0"),
    openLiabilityUsd: usd("open_liability_usd").notNull().default("0"),
    longValueUsd: usd("long_value_usd").notNull().default("0"),
    collateralLockedUsd: usd("collateral_locked_usd").notNull().default("0"),
    maxProfitUsd: usd("max_profit_usd"),
    maxLossUsd: usd("max_loss_usd").notNull().default("0"),
    unrealizedPnl: usd("unrealized_pnl").notNull().default("0"),
    realizedPnl: usd("realized_pnl").notNull().default("0"),
    feesPaid: usd("fees_paid").notNull().default("0"),
    netDelta: doublePrecision("net_delta").notNull().default(0),
    netTheta: doublePrecision("net_theta").notNull().default(0),
    netVega: doublePrecision("net_vega").notNull().default(0),
    pinRiskWarning: boolean("pin_risk_warning").notNull().default(false),
    earlyAssignmentWarning: boolean("early_assignment_warning").notNull().default(false),
    wheelState: varchar("wheel_state", { length: 32 }),
    aiRationale: text("ai_rationale"),
    closeReason: text("close_reason"),
    payoffCurve: jsonb("payoff_curve").notNull().default([]),
    metadata: jsonb("metadata").notNull().default({}),
    openedAt: ts("opened_at").notNull().defaultNow(),
    closedAt: ts("closed_at"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [
    index("idx_opt_strategies_account_status").on(t.accountSlug, t.status),
    index("idx_opt_strategies_account_id").on(t.accountId),
  ],
);

export const optLegs = pgTable(
  "opt_legs",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    strategyId: uuid("strategy_id")
      .notNull()
      .references(() => optStrategies.id, { onDelete: "cascade" }),
    contractId: uuid("contract_id").references(() => optContracts.id),
    occSymbol: varchar("occ_symbol", { length: 32 }),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    legRole: varchar("leg_role", { length: 32 }).notNull(),
    side: varchar("side", { length: 8 }).notNull(), // 'long' | 'short'
    optionRight: varchar("option_right", { length: 8 }),
    strike: px("strike"),
    expirationDate: date("expiration_date", { mode: "string" }),
    multiplier: integer("multiplier").notNull().default(100),
    quantity: integer("quantity").notNull().default(1),
    entryPrice: px("entry_price").notNull().default("0"),
    currentMark: px("current_mark").notNull().default("0"),
    bid: px("bid"),
    ask: px("ask"),
    unrealizedPnl: usd("unrealized_pnl").notNull().default("0"),
    realizedPnl: usd("realized_pnl").notNull().default("0"),
    delta: doublePrecision("delta"),
    gamma: doublePrecision("gamma"),
    theta: doublePrecision("theta"),
    vega: doublePrecision("vega"),
    impliedVolatility: doublePrecision("implied_volatility"),
    status: varchar("status", { length: 24 }).notNull().default("open"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [
    index("idx_opt_legs_strategy").on(t.strategyId),
    index("idx_opt_legs_occ").on(t.occSymbol),
  ],
);

export const optOrders = pgTable(
  "opt_orders",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => optAccounts.id),
    strategyId: uuid("strategy_id").references(() => optStrategies.id),
    actionKind: varchar("action_kind", { length: 32 }).notNull(),
    legsPayload: jsonb("legs_payload").notNull().default([]),
    orderStatus: varchar("order_status", { length: 24 }).notNull().default("filled"),
    idempotencyKey: varchar("idempotency_key", { length: 128 }).notNull().unique(),
    createdAt: ts("created_at").notNull().defaultNow(),
    filledAt: ts("filled_at"),
  },
  (t) => [
    index("idx_opt_orders_account").on(t.accountId, t.createdAt.desc()),
    index("idx_opt_orders_strategy").on(t.strategyId),
  ],
);

export const optFills = pgTable(
  "opt_fills",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    orderId: uuid("order_id")
      .notNull()
      .references(() => optOrders.id),
    strategyId: uuid("strategy_id")
      .notNull()
      .references(() => optStrategies.id),
    legId: uuid("leg_id")
      .notNull()
      .references(() => optLegs.id),
    occSymbol: varchar("occ_symbol", { length: 32 }),
    side: varchar("side", { length: 8 }).notNull(),
    quantity: integer("quantity").notNull(),
    multiplier: integer("multiplier").notNull().default(100),
    fillPrice: px("fill_price").notNull(),
    bidAtFill: px("bid_at_fill"),
    askAtFill: px("ask_at_fill"),
    slippageUsd: px("slippage_usd").notNull().default("0"),
    feeUsd: px("fee_usd").notNull().default("0"),
    cashFlowUsd: usd("cash_flow_usd").notNull().default("0"),
    feedTier: varchar("feed_tier", { length: 32 }).notNull().default("alpaca_indicative"),
    isIndicative: boolean("is_indicative").notNull().default(true),
    filledAt: ts("filled_at").notNull().defaultNow(),
  },
  (t) => [
    index("idx_opt_fills_order").on(t.orderId),
    index("idx_opt_fills_strategy").on(t.strategyId),
    index("idx_opt_fills_leg").on(t.legId),
  ],
);

export const optStockLots = pgTable(
  "opt_stock_lots",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => optAccounts.id),
    accountSlug: varchar("account_slug", { length: 64 }).notNull(),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    shares: integer("shares").notNull().default(0),
    avgCostBasis: px("avg_cost_basis").notNull().default("0"),
    encumberedShares: integer("encumbered_shares").notNull().default(0),
    sourceStrategyId: uuid("source_strategy_id"),
    updatedAt: ts("updated_at").notNull().defaultNow(),
  },
  (t) => [unique("opt_stock_lots_account_slug_underlying_symbol_key").on(t.accountSlug, t.underlyingSymbol)],
);

export const optLedgerEvents = pgTable(
  "opt_ledger_events",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    accountId: uuid("account_id")
      .notNull()
      .references(() => optAccounts.id),
    accountSlug: varchar("account_slug", { length: 64 }).notNull(),
    strategyId: uuid("strategy_id"),
    eventType: varchar("event_type", { length: 32 }).notNull(),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull().default(""),
    strategyType: varchar("strategy_type", { length: 64 }).notNull().default(""),
    cashDelta: usd("cash_delta").notNull().default("0"),
    realizedPnlDelta: usd("realized_pnl_delta").notNull().default("0"),
    collateralDelta: usd("collateral_delta").notNull().default("0"),
    feeUsd: usd("fee_usd").notNull().default("0"),
    description: text("description").notNull().default(""),
    metadata: jsonb("metadata").notNull().default({}),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [index("idx_opt_ledger_account_ts").on(t.accountSlug, t.createdAt.desc())],
);

export const optAiDecisions = pgTable(
  "opt_ai_decisions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    decisionId: varchar("decision_id", { length: 64 }).notNull().unique(),
    accountSlug: varchar("account_slug", { length: 64 }).notNull(),
    mode: varchar("mode", { length: 16 }).notNull().default("ai"),
    action: varchar("action", { length: 16 }).notNull(),
    strategyType: varchar("strategy_type", { length: 64 }),
    candidateId: varchar("candidate_id", { length: 128 }),
    positionGroupId: varchar("position_group_id", { length: 64 }),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    contractsQty: integer("contracts_qty").notNull().default(1),
    confidence: doublePrecision("confidence").notNull().default(0),
    decidedBy: varchar("decided_by", { length: 64 }).notNull().default("rules_engine"),
    modelName: varchar("model_name", { length: 64 }),
    rationaleHe: text("rationale_he").notNull().default(""),
    rationaleEn: text("rationale_en").notNull().default(""),
    riskGuardPassed: boolean("risk_guard_passed").notNull().default(false),
    riskGuardReasons: jsonb("risk_guard_reasons").notNull().default([]),
    marketContext: jsonb("market_context").notNull().default({}),
    createdAt: ts("created_at").notNull().defaultNow(),
  },
  (t) => [index("idx_opt_ai_decisions_account_ts").on(t.accountSlug, t.createdAt.desc())],
);

export const optOpportunities = pgTable(
  "opt_opportunities",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    candidateId: varchar("candidate_id", { length: 128 }).notNull().unique(),
    strategyType: varchar("strategy_type", { length: 64 }).notNull(),
    underlyingSymbol: varchar("underlying_symbol", { length: 16 }).notNull(),
    expirationDate: date("expiration_date", { mode: "string" }).notNull(),
    payload: jsonb("payload").notNull(),
    rulesScore: doublePrecision("rules_score").notNull().default(0),
    feedTier: varchar("feed_tier", { length: 32 }).notNull().default("alpaca_indicative"),
    isIndicative: boolean("is_indicative").notNull().default(true),
    scannedAt: ts("scanned_at").notNull().defaultNow(),
  },
  (t) => [index("idx_opt_opportunities_scanned").on(t.scannedAt.desc())],
);

export const optEngineState = pgTable("opt_engine_state", {
  id: integer("id").primaryKey().default(1),
  enabled: boolean("enabled").notNull().default(false),
  runningCycle: boolean("running_cycle").notNull().default(false),
  mode: varchar("mode", { length: 16 }).notNull().default("ai"),
  feedTier: varchar("feed_tier", { length: 32 }).notNull().default("alpaca_indicative"),
  isIndicative: boolean("is_indicative").notNull().default(true),
  isDelayedTrades: boolean("is_delayed_trades").notNull().default(true),
  marketOpen: boolean("market_open").notNull().default(false),
  dataFreshnessSeconds: doublePrecision("data_freshness_seconds"),
  lastCycleAt: ts("last_cycle_at"),
  lastError: text("last_error"),
  groqAvailable: boolean("groq_available").notNull().default(false),
  groqModel: varchar("groq_model", { length: 64 }).notNull().default("openai/gpt-oss-120b"),
  groqCallsToday: integer("groq_calls_today").notNull().default(0),
  groqCallsDate: date("groq_calls_date", { mode: "string" }),
  universe: jsonb("universe")
    .notNull()
    .default(["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "PLTR", "SOFI", "INTC"]),
  updatedAt: ts("updated_at").notNull().defaultNow(),
});
