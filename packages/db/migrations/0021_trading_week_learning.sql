-- Trading Week Learning Layer — observational shadow tables only.
-- These tables must NEVER be joined into Research / Live Sim financial truth.

BEGIN;

CREATE TABLE IF NOT EXISTS trading_week_baselines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  week_label VARCHAR(16) NOT NULL,
  activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  commit_sha VARCHAR(64),
  snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trading_week_baselines_active
  ON trading_week_baselines ((is_active))
  WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS learning_eval_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  candle_timestamp TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  strategy_slug VARCHAR(64) NOT NULL,
  robot_label VARCHAR(32),
  paper_run_id UUID,
  direction VARCHAR(8),
  active_action VARCHAR(16) NOT NULL,
  active_reason TEXT,
  structure_regime VARCHAR(32),
  volatility_regime VARCHAR(32),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (symbol, timeframe, candle_timestamp, strategy_slug)
);

CREATE INDEX IF NOT EXISTS idx_learning_eval_events_week
  ON learning_eval_events (evaluated_at, strategy_slug, symbol);

CREATE TABLE IF NOT EXISTS learning_shadow_rsi_evals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  eval_event_id UUID REFERENCES learning_eval_events(id) ON DELETE SET NULL,
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  candle_timestamp TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  strategy_slug VARCHAR(64) NOT NULL DEFAULT 'gold-trend-pullback',
  paper_run_id UUID,
  active_action VARCHAR(16) NOT NULL,
  active_reason TEXT,
  shadow_directional_action VARCHAR(16) NOT NULL,
  shadow_directional_reason TEXT,
  shadow_no_rsi_action VARCHAR(16) NOT NULL,
  shadow_no_rsi_reason TEXT,
  rsi NUMERIC(18, 8),
  ema20 NUMERIC(18, 8),
  ema50 NUMERIC(18, 8),
  ema200 NUMERIC(18, 8),
  atr NUMERIC(18, 8),
  suggested_sl NUMERIC(18, 8),
  suggested_tp NUMERIC(18, 8),
  structure_regime VARCHAR(32),
  volatility_regime VARCHAR(32),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (symbol, timeframe, candle_timestamp, strategy_slug)
);

CREATE INDEX IF NOT EXISTS idx_learning_shadow_rsi_evals_ts
  ON learning_shadow_rsi_evals (candle_timestamp, symbol);

CREATE TABLE IF NOT EXISTS learning_shadow_rsi_trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  eval_id UUID REFERENCES learning_shadow_rsi_evals(id) ON DELETE SET NULL,
  variant VARCHAR(32) NOT NULL,
  symbol VARCHAR(32) NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  direction VARCHAR(8) NOT NULL,
  signal_candle_ts TIMESTAMPTZ NOT NULL,
  entry_candle_ts TIMESTAMPTZ,
  planned_entry_ref NUMERIC(18, 8),
  fill_price NUMERIC(18, 8),
  stop_loss NUMERIC(18, 8),
  take_profit NUMERIC(18, 8),
  quantity NUMERIC(18, 8) NOT NULL DEFAULT 1,
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  exit_reason VARCHAR(32),
  exit_price NUMERIC(18, 8),
  exit_ts TIMESTAMPTZ,
  gross_pnl NUMERIC(18, 8),
  net_pnl NUMERIC(18, 8),
  fees NUMERIC(18, 8) DEFAULT 0,
  spread_cost NUMERIC(18, 8) DEFAULT 0,
  slippage_cost NUMERIC(18, 8) DEFAULT 0,
  duration_seconds INTEGER,
  structure_regime VARCHAR(32),
  volatility_regime VARCHAR(32),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (variant, symbol, timeframe, signal_candle_ts, direction)
);

CREATE INDEX IF NOT EXISTS idx_learning_shadow_rsi_trades_open
  ON learning_shadow_rsi_trades (status, symbol, timeframe)
  WHERE status = 'open';

CREATE TABLE IF NOT EXISTS learning_daily_loss_shadow (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  portfolio_id UUID NOT NULL,
  trade_date DATE NOT NULL,
  daily_loss_limit_pct NUMERIC(10, 4) NOT NULL,
  start_equity NUMERIC(18, 4) NOT NULL,
  shadow_halt BOOLEAN NOT NULL DEFAULT FALSE,
  shadow_halt_time TIMESTAMPTZ,
  threshold_pct NUMERIC(10, 4),
  equity_at_halt NUMERIC(18, 4),
  actual_eod_pnl NUMERIC(18, 4),
  shadow_stop_eod_pnl NUMERIC(18, 4),
  difference NUMERIC(18, 4),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (portfolio_id, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_learning_daily_loss_shadow_date
  ON learning_daily_loss_shadow (trade_date);

CREATE TABLE IF NOT EXISTS learning_planned_vs_actual_risk (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  source_type VARCHAR(32) NOT NULL,
  portfolio_id UUID,
  broker_account_id UUID,
  position_id UUID,
  trade_id UUID,
  live_sim_position_id UUID,
  symbol VARCHAR(32) NOT NULL,
  timeframe VARCHAR(8),
  direction VARCHAR(8) NOT NULL,
  signal_candle_close NUMERIC(18, 8),
  planned_entry_ref NUMERIC(18, 8),
  execution_candle_open NUMERIC(18, 8),
  actual_fill_price NUMERIC(18, 8) NOT NULL,
  gap_from_signal NUMERIC(18, 8),
  spread NUMERIC(18, 8),
  slippage NUMERIC(18, 8),
  fees NUMERIC(18, 8),
  planned_sl NUMERIC(18, 8),
  planned_tp NUMERIC(18, 8),
  planned_risk_usd NUMERIC(18, 8),
  planned_risk_pct NUMERIC(18, 8),
  actual_risk_usd NUMERIC(18, 8),
  actual_risk_pct NUMERIC(18, 8),
  risk_diff_usd NUMERIC(18, 8),
  risk_diff_pct NUMERIC(18, 8),
  planned_rr NUMERIC(18, 8),
  actual_rr NUMERIC(18, 8),
  risk_overrun BOOLEAN NOT NULL DEFAULT FALSE,
  equity_at_entry NUMERIC(18, 4),
  quantity NUMERIC(18, 8),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_pva_research_position
  ON learning_planned_vs_actual_risk (position_id)
  WHERE position_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_pva_live_sim_position
  ON learning_planned_vs_actual_risk (live_sim_position_id)
  WHERE live_sim_position_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_learning_pva_created
  ON learning_planned_vs_actual_risk (created_at, source_type);

CREATE TABLE IF NOT EXISTS learning_funnel_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  baseline_id UUID REFERENCES trading_week_baselines(id) ON DELETE SET NULL,
  event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  candle_timestamp TIMESTAMPTZ,
  strategy_slug VARCHAR(64),
  robot_label VARCHAR(32),
  symbol VARCHAR(32),
  timeframe VARCHAR(8),
  direction VARCHAR(8),
  stage VARCHAR(64) NOT NULL,
  reason TEXT,
  source_table VARCHAR(64),
  source_id UUID,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_learning_funnel_events_week
  ON learning_funnel_events (event_at, stage, strategy_slug);

COMMIT;
