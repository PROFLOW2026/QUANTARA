import { pgEnum } from "drizzle-orm/pg-core";

export const assetClassEnum = pgEnum("asset_class", [
  "commodity",
  "forex",
  "index",
  "crypto",
  "stock",
]);

export const timeframeEnum = pgEnum("timeframe", ["5m", "15m", "1h"]);

export const strategyStatusEnum = pgEnum("strategy_status", [
  "draft",
  "active",
  "deprecated",
  "archived",
]);

export const riskProfileSlugEnum = pgEnum("risk_profile_slug", [
  "conservative",
  "balanced",
  "aggressive",
]);

export const portfolioModeEnum = pgEnum("portfolio_mode", [
  "backtest",
  "paper",
  "live_manual",
  "live_automated",
]);

export const portfolioStatusEnum = pgEnum("portfolio_status", [
  "active",
  "halted",
  "closed",
]);

export const signalActionEnum = pgEnum("signal_action", [
  "buy",
  "sell",
  "hold",
  "close",
  "modify",
]);

export const decisionTypeEnum = pgEnum("decision_type", [
  "hold",
  "buy_signal",
  "sell_signal",
  "close_signal",
  "no_setup",
  "risk_denied",
  "risk_approved",
  "position_open",
  "trading_halted",
  "execution_failed",
  "sl_triggered",
  "tp_triggered",
  "system_error",
]);

export const directionEnum = pgEnum("direction", ["long", "short"]);

export const entryTypeEnum = pgEnum("entry_type", ["market", "limit"]);

export const orderIntentStatusEnum = pgEnum("order_intent_status", [
  "pending_execution",
  "executed",
  "rejected",
  "expired",
]);

export const orderTypeEnum = pgEnum("order_type", ["market", "limit"]);

export const orderStatusEnum = pgEnum("order_status", [
  "pending",
  "submitted",
  "filled",
  "partial",
  "cancelled",
  "rejected",
]);

export const fillSideEnum = pgEnum("fill_side", ["entry", "exit"]);

export const positionStatusEnum = pgEnum("position_status", [
  "open",
  "closed",
]);

export const exitReasonEnum = pgEnum("exit_reason", [
  "sl",
  "tp",
  "strategy",
  "manual",
  "risk_halt",
  "end_of_backtest",
]);

export const backtestStatusEnum = pgEnum("backtest_status", [
  "pending",
  "running",
  "completed",
  "failed",
]);

export const experimentStatusEnum = pgEnum("experiment_status", [
  "draft",
  "running",
  "completed",
]);

export const jobTypeEnum = pgEnum("job_type", [
  "fetch_data",
  "run_strategy",
  "check_sl_tp",
  "snapshot",
  "backtest",
]);

export const workerJobStatusEnum = pgEnum("worker_job_status", [
  "pending",
  "running",
  "completed",
  "failed",
]);

export const workerRunStatusEnum = pgEnum("worker_run_status", [
  "success",
  "partial",
  "failed",
]);

export const eventSeverityEnum = pgEnum("event_severity", [
  "info",
  "warning",
  "error",
  "critical",
]);

/** Alias for run_context on trading records — same values as portfolio_mode */
export const runModeEnum = portfolioModeEnum;
