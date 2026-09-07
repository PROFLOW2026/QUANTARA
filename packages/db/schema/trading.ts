import {
  index,
  integer,
  jsonb,
  numeric,
  pgTable,
  text,
  timestamp,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import {
  decisionTypeEnum,
  directionEnum,
  entryTypeEnum,
  exitReasonEnum,
  fillSideEnum,
  orderIntentStatusEnum,
  orderStatusEnum,
  orderTypeEnum,
  portfolioModeEnum,
  positionStatusEnum,
  signalActionEnum,
} from "./enums";
import { instruments } from "./instruments";
import { backtestRuns, portfolios, riskProfiles, strategyInstances } from "./portfolio";
import { strategyVersions } from "./strategies";

export const signals = pgTable(
  "signals",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    strategyInstanceId: uuid("strategy_instance_id")
      .notNull()
      .references(() => strategyInstances.id),
    strategyVersionId: uuid("strategy_version_id")
      .notNull()
      .references(() => strategyVersions.id),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    candleTimestamp: timestamp("candle_timestamp", {
      withTimezone: true,
      mode: "string",
    }).notNull(),
    action: signalActionEnum("action").notNull(),
    reason: text("reason").notNull(),
    confidence: numeric("confidence", { precision: 5, scale: 4 }),
    suggestedSl: numeric("suggested_sl", { precision: 18, scale: 8 }),
    suggestedTp: numeric("suggested_tp", { precision: 18, scale: 8 }),
    metadata: jsonb("metadata"),
    mode: portfolioModeEnum("mode").notNull(),
    backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("signals_strategy_instance_candle_timestamp_idx").on(
      table.strategyInstanceId,
      table.candleTimestamp,
    ),
  ],
);

export const decisions = pgTable(
  "decisions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    strategyInstanceId: uuid("strategy_instance_id")
      .notNull()
      .references(() => strategyInstances.id),
    signalId: uuid("signal_id").references(() => signals.id),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    candleTimestamp: timestamp("candle_timestamp", {
      withTimezone: true,
      mode: "string",
    }).notNull(),
    decisionType: decisionTypeEnum("decision_type").notNull(),
    message: text("message").notNull(),
    metadata: jsonb("metadata"),
    mode: portfolioModeEnum("mode").notNull(),
    backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("decisions_strategy_instance_created_at_idx").on(
      table.strategyInstanceId,
      table.createdAt,
    ),
    index("decisions_created_at_idx").on(table.createdAt),
  ],
);

export const orderIntents = pgTable("order_intents", {
  id: uuid("id").primaryKey().defaultRandom(),
  signalId: uuid("signal_id")
    .notNull()
    .references(() => signals.id),
  strategyInstanceId: uuid("strategy_instance_id")
    .notNull()
    .references(() => strategyInstances.id),
  portfolioId: uuid("portfolio_id")
    .notNull()
    .references(() => portfolios.id),
  direction: directionEnum("direction").notNull(),
  quantity: numeric("quantity", { precision: 18, scale: 8 }).notNull(),
  entryType: entryTypeEnum("entry_type").notNull(),
  limitPrice: numeric("limit_price", { precision: 18, scale: 8 }),
  stopLoss: numeric("stop_loss", { precision: 18, scale: 8 }).notNull(),
  takeProfit: numeric("take_profit", { precision: 18, scale: 8 }),
  targetRiskAmount: numeric("target_risk_amount", {
    precision: 18,
    scale: 2,
  }).notNull(),
  actualRiskAmount: numeric("actual_risk_amount", {
    precision: 18,
    scale: 2,
  }).notNull(),
  signalCandleTimestamp: timestamp("signal_candle_timestamp", {
    withTimezone: true,
    mode: "string",
  }).notNull(),
  executionCandleTimestamp: timestamp("execution_candle_timestamp", {
    withTimezone: true,
    mode: "string",
  }),
  riskProfileId: uuid("risk_profile_id")
    .notNull()
    .references(() => riskProfiles.id),
  status: orderIntentStatusEnum("status").notNull().default("pending_execution"),
  rejectionReason: text("rejection_reason"),
  idempotencyKey: varchar("idempotency_key", { length: 100 }).notNull().unique(),
  mode: portfolioModeEnum("mode").notNull(),
  backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const orders = pgTable("orders", {
  id: uuid("id").primaryKey().defaultRandom(),
  intentId: uuid("intent_id")
    .notNull()
    .references(() => orderIntents.id),
  portfolioId: uuid("portfolio_id")
    .notNull()
    .references(() => portfolios.id),
  instrumentId: uuid("instrument_id")
    .notNull()
    .references(() => instruments.id),
  direction: directionEnum("direction").notNull(),
  quantity: numeric("quantity", { precision: 18, scale: 8 }).notNull(),
  orderType: orderTypeEnum("order_type").notNull(),
  status: orderStatusEnum("status").notNull().default("pending"),
  brokerOrderId: varchar("broker_order_id", { length: 100 }),
  submittedAt: timestamp("submitted_at", { withTimezone: true, mode: "string" }),
  mode: portfolioModeEnum("mode").notNull(),
  backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const positions = pgTable(
  "positions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    portfolioId: uuid("portfolio_id")
      .notNull()
      .references(() => portfolios.id),
    strategyInstanceId: uuid("strategy_instance_id")
      .notNull()
      .references(() => strategyInstances.id),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    direction: directionEnum("direction").notNull(),
    quantity: numeric("quantity", { precision: 18, scale: 8 }).notNull(),
    entryPrice: numeric("entry_price", { precision: 18, scale: 8 }).notNull(),
    currentPrice: numeric("current_price", { precision: 18, scale: 8 }).notNull(),
    stopLoss: numeric("stop_loss", { precision: 18, scale: 8 }).notNull(),
    takeProfit: numeric("take_profit", { precision: 18, scale: 8 }),
    unrealizedPnl: numeric("unrealized_pnl", { precision: 18, scale: 2 }).notNull(),
    status: positionStatusEnum("status").notNull().default("open"),
    openedAt: timestamp("opened_at", { withTimezone: true, mode: "string" }).notNull(),
    closedAt: timestamp("closed_at", { withTimezone: true, mode: "string" }),
    mode: portfolioModeEnum("mode").notNull(),
    backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("positions_portfolio_status_idx").on(table.portfolioId, table.status),
  ],
);

export const fills = pgTable("fills", {
  id: uuid("id").primaryKey().defaultRandom(),
  orderId: uuid("order_id")
    .notNull()
    .references(() => orders.id),
  positionId: uuid("position_id").references(() => positions.id),
  fillPrice: numeric("fill_price", { precision: 18, scale: 8 }).notNull(),
  fillQuantity: numeric("fill_quantity", { precision: 18, scale: 8 }).notNull(),
  fees: numeric("fees", { precision: 18, scale: 4 }).notNull().default("0"),
  slippage: numeric("slippage", { precision: 18, scale: 8 }).notNull().default("0"),
  spreadCost: numeric("spread_cost", { precision: 18, scale: 4 })
    .notNull()
    .default("0"),
  basePrice: numeric("base_price", { precision: 18, scale: 8 }),
  side: fillSideEnum("side").notNull(),
  filledAt: timestamp("filled_at", { withTimezone: true, mode: "string" }).notNull(),
  mode: portfolioModeEnum("mode").notNull(),
  backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const trades = pgTable(
  "trades",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    positionId: uuid("position_id")
      .notNull()
      .references(() => positions.id),
    portfolioId: uuid("portfolio_id")
      .notNull()
      .references(() => portfolios.id),
    strategyInstanceId: uuid("strategy_instance_id")
      .notNull()
      .references(() => strategyInstances.id),
    strategyVersionId: uuid("strategy_version_id")
      .notNull()
      .references(() => strategyVersions.id),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    direction: directionEnum("direction").notNull(),
    quantity: numeric("quantity", { precision: 18, scale: 8 }).notNull(),
    entryPrice: numeric("entry_price", { precision: 18, scale: 8 }).notNull(),
    exitPrice: numeric("exit_price", { precision: 18, scale: 8 }).notNull(),
    grossPnl: numeric("gross_pnl", { precision: 18, scale: 2 }).notNull(),
    realizedPnl: numeric("realized_pnl", { precision: 18, scale: 2 }).notNull(),
    feesTotal: numeric("fees_total", { precision: 18, scale: 4 }).notNull(),
    slippageTotal: numeric("slippage_total", { precision: 18, scale: 8 }).notNull(),
    spreadTotal: numeric("spread_total", { precision: 18, scale: 4 }).notNull(),
    targetRiskAmount: numeric("target_risk_amount", {
      precision: 18,
      scale: 2,
    }).notNull(),
    actualRiskAmount: numeric("actual_risk_amount", {
      precision: 18,
      scale: 2,
    }).notNull(),
    exitReason: exitReasonEnum("exit_reason").notNull(),
    durationSeconds: integer("duration_seconds").notNull(),
    openedAt: timestamp("opened_at", { withTimezone: true, mode: "string" }).notNull(),
    closedAt: timestamp("closed_at", { withTimezone: true, mode: "string" }).notNull(),
    mode: portfolioModeEnum("mode").notNull(),
    backtestRunId: uuid("backtest_run_id").references(() => backtestRuns.id),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("trades_strategy_version_closed_at_idx").on(
      table.strategyVersionId,
      table.closedAt,
    ),
  ],
);
