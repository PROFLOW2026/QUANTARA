import {
  boolean,
  integer,
  jsonb,
  numeric,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import {
  backtestStatusEnum,
  experimentStatusEnum,
  portfolioModeEnum,
  portfolioStatusEnum,
  riskProfileSlugEnum,
  timeframeEnum,
} from "./enums";
import { instruments } from "./instruments";
import { strategyVersions } from "./strategies";

export const riskProfiles = pgTable("risk_profiles", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: varchar("name", { length: 50 }).notNull(),
  slug: riskProfileSlugEnum("slug").notNull().unique(),
  riskPerTradePct: numeric("risk_per_trade_pct").notNull(),
  maxOpenPositions: integer("max_open_positions").notNull(),
  maxTotalExposurePct: numeric("max_total_exposure_pct").notNull(),
  dailyLossLimitPct: numeric("daily_loss_limit_pct").notNull(),
  maxDrawdownPct: numeric("max_drawdown_pct").notNull(),
  volatilityLimitAtrMult: numeric("volatility_limit_atr_mult"),
  isDefault: boolean("is_default").notNull().default(false),
  parameters: jsonb("parameters"),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const portfolios = pgTable("portfolios", {
  id: uuid("id").primaryKey().defaultRandom(),
  ownerId: uuid("owner_id").notNull(),
  name: varchar("name", { length: 100 }).notNull(),
  mode: portfolioModeEnum("mode").notNull(),
  initialCapital: numeric("initial_capital", { precision: 18, scale: 2 }).notNull(),
  balance: numeric("balance", { precision: 18, scale: 2 }).notNull(),
  unrealizedPnl: numeric("unrealized_pnl", { precision: 18, scale: 2 })
    .notNull()
    .default("0"),
  equity: numeric("equity", { precision: 18, scale: 2 }).notNull(),
  exposureNotional: numeric("exposure_notional", { precision: 18, scale: 2 })
    .notNull()
    .default("0"),
  reservedCapital: numeric("reserved_capital", { precision: 18, scale: 2 })
    .notNull()
    .default("0"),
  currency: varchar("currency", { length: 10 }).notNull().default("USD"),
  status: portfolioStatusEnum("status").notNull().default("active"),
  haltReason: text("halt_reason"),
  peakEquity: numeric("peak_equity", { precision: 18, scale: 2 }).notNull(),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const experiments = pgTable("experiments", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: varchar("name", { length: 100 }).notNull(),
  description: text("description"),
  instrumentId: uuid("instrument_id")
    .notNull()
    .references(() => instruments.id),
  status: experimentStatusEnum("status").notNull().default("draft"),
  startDate: timestamp("start_date", { withTimezone: true, mode: "string" }).notNull(),
  endDate: timestamp("end_date", { withTimezone: true, mode: "string" }),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const strategyInstances = pgTable(
  "strategy_instances",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    portfolioId: uuid("portfolio_id")
      .notNull()
      .references(() => portfolios.id),
    strategyVersionId: uuid("strategy_version_id")
      .notNull()
      .references(() => strategyVersions.id),
    instrumentId: uuid("instrument_id")
      .notNull()
      .references(() => instruments.id),
    timeframe: timeframeEnum("timeframe").notNull(),
    riskProfileId: uuid("risk_profile_id")
      .notNull()
      .references(() => riskProfiles.id),
    parameterOverrides: jsonb("parameter_overrides"),
    isActive: boolean("is_active").notNull().default(true),
    experimentId: uuid("experiment_id").references(() => experiments.id),
    createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    uniqueIndex("strategy_instances_active_unique")
      .on(
        table.portfolioId,
        table.strategyVersionId,
        table.instrumentId,
        table.timeframe,
      )
      .where(sql`${table.isActive} = true`),
  ],
);

export const portfolioSnapshots = pgTable("portfolio_snapshots", {
  id: uuid("id").primaryKey().defaultRandom(),
  portfolioId: uuid("portfolio_id")
    .notNull()
    .references(() => portfolios.id),
  timestamp: timestamp("timestamp", { withTimezone: true, mode: "string" }).notNull(),
  balance: numeric("balance", { precision: 18, scale: 2 }).notNull(),
  equity: numeric("equity", { precision: 18, scale: 2 }).notNull(),
  exposureNotional: numeric("exposure_notional", { precision: 18, scale: 2 }).notNull(),
  reservedCapital: numeric("reserved_capital", { precision: 18, scale: 2 }).notNull(),
  unrealizedPnl: numeric("unrealized_pnl", { precision: 18, scale: 2 }).notNull(),
  drawdownPct: numeric("drawdown_pct", { precision: 8, scale: 4 }).notNull(),
  openPositionsCount: integer("open_positions_count").notNull(),
  metadata: jsonb("metadata"),
  mode: portfolioModeEnum("mode").notNull(),
  backtestRunId: uuid("backtest_run_id"),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});

export const backtestRuns = pgTable("backtest_runs", {
  id: uuid("id").primaryKey().defaultRandom(),
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
  timeframe: timeframeEnum("timeframe").notNull(),
  startDate: timestamp("start_date", { withTimezone: true, mode: "string" }).notNull(),
  endDate: timestamp("end_date", { withTimezone: true, mode: "string" }).notNull(),
  initialCapital: numeric("initial_capital", { precision: 18, scale: 2 }).notNull(),
  finalCapital: numeric("final_capital", { precision: 18, scale: 2 }),
  status: backtestStatusEnum("status").notNull().default("pending"),
  parameters: jsonb("parameters").notNull(),
  executionAssumptions: jsonb("execution_assumptions").notNull(),
  datasetFingerprint: varchar("dataset_fingerprint", { length: 64 }).notNull(),
  metrics: jsonb("metrics"),
  startedAt: timestamp("started_at", { withTimezone: true, mode: "string" }),
  completedAt: timestamp("completed_at", { withTimezone: true, mode: "string" }),
  errorMessage: text("error_message"),
  createdAt: timestamp("created_at", { withTimezone: true, mode: "string" })
    .notNull()
    .defaultNow(),
});
