-- QUANTARA initial schema migration
-- Source of truth: packages/db/schema/

BEGIN;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

CREATE TYPE asset_class AS ENUM ('commodity', 'forex', 'index', 'crypto', 'stock');
CREATE TYPE timeframe AS ENUM ('5m', '15m', '1h');
CREATE TYPE strategy_status AS ENUM ('draft', 'active', 'deprecated', 'archived');
CREATE TYPE risk_profile_slug AS ENUM ('conservative', 'balanced', 'aggressive');
CREATE TYPE portfolio_mode AS ENUM ('backtest', 'paper', 'live_manual', 'live_automated');
CREATE TYPE portfolio_status AS ENUM ('active', 'halted', 'closed');
CREATE TYPE signal_action AS ENUM ('buy', 'sell', 'hold', 'close', 'modify');
CREATE TYPE decision_type AS ENUM (
  'hold', 'buy_signal', 'sell_signal', 'close_signal', 'no_setup',
  'risk_denied', 'risk_approved', 'position_open', 'trading_halted',
  'execution_failed', 'sl_triggered', 'tp_triggered', 'system_error'
);
CREATE TYPE direction AS ENUM ('long', 'short');
CREATE TYPE entry_type AS ENUM ('market', 'limit');
CREATE TYPE order_intent_status AS ENUM ('pending_execution', 'executed', 'rejected', 'expired');
CREATE TYPE order_type AS ENUM ('market', 'limit');
CREATE TYPE order_status AS ENUM ('pending', 'submitted', 'filled', 'partial', 'cancelled', 'rejected');
CREATE TYPE fill_side AS ENUM ('entry', 'exit');
CREATE TYPE position_status AS ENUM ('open', 'closed');
CREATE TYPE exit_reason AS ENUM ('sl', 'tp', 'strategy', 'manual', 'risk_halt', 'end_of_backtest');
CREATE TYPE backtest_status AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE experiment_status AS ENUM ('draft', 'running', 'completed');
CREATE TYPE job_type AS ENUM ('fetch_data', 'run_strategy', 'check_sl_tp', 'snapshot', 'backtest');
CREATE TYPE worker_job_status AS ENUM ('pending', 'running', 'completed', 'failed');
CREATE TYPE worker_run_status AS ENUM ('success', 'partial', 'failed');
CREATE TYPE event_severity AS ENUM ('info', 'warning', 'error', 'critical');

-- ---------------------------------------------------------------------------
-- Core reference tables
-- ---------------------------------------------------------------------------

CREATE TABLE instruments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  asset_class asset_class NOT NULL,
  base_currency VARCHAR(10) NOT NULL,
  quote_currency VARCHAR(10) NOT NULL,
  pip_size NUMERIC NOT NULL,
  contract_size NUMERIC NOT NULL,
  price_tick_size NUMERIC NOT NULL,
  quantity_step NUMERIC NOT NULL,
  min_quantity NUMERIC NOT NULL,
  trading_sessions JSONB,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE candles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  timeframe timeframe NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  open NUMERIC(18, 8) NOT NULL,
  high NUMERIC(18, 8) NOT NULL,
  low NUMERIC(18, 8) NOT NULL,
  close NUMERIC(18, 8) NOT NULL,
  volume NUMERIC(18, 4),
  source VARCHAR(50) NOT NULL,
  is_complete BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT candles_instrument_timeframe_timestamp_source_unique
    UNIQUE (instrument_id, timeframe, timestamp, source)
);

CREATE INDEX candles_instrument_timeframe_timestamp_idx
  ON candles (instrument_id, timeframe, timestamp DESC);

CREATE TABLE strategies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(50) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  description TEXT,
  supported_instruments UUID[] NOT NULL,
  supported_timeframes timeframe[] NOT NULL,
  status strategy_status NOT NULL DEFAULT 'draft',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE strategy_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id UUID NOT NULL REFERENCES strategies(id),
  version VARCHAR(20) NOT NULL,
  version_major INT NOT NULL,
  version_minor INT NOT NULL,
  version_patch INT NOT NULL,
  parameters JSONB NOT NULL,
  parameters_schema JSONB NOT NULL,
  risk_profile_compatibility risk_profile_slug[] NOT NULL,
  logic_hash VARCHAR(64) NOT NULL,
  changelog TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT strategy_versions_strategy_id_version_unique UNIQUE (strategy_id, version)
);

CREATE TABLE risk_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(50) NOT NULL,
  slug risk_profile_slug NOT NULL UNIQUE,
  risk_per_trade_pct NUMERIC NOT NULL,
  max_open_positions INT NOT NULL,
  max_total_exposure_pct NUMERIC NOT NULL,
  daily_loss_limit_pct NUMERIC NOT NULL,
  max_drawdown_pct NUMERIC NOT NULL,
  volatility_limit_atr_mult NUMERIC,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  parameters JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE portfolios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL,
  name VARCHAR(100) NOT NULL,
  mode portfolio_mode NOT NULL,
  initial_capital NUMERIC(18, 2) NOT NULL,
  balance NUMERIC(18, 2) NOT NULL,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  equity NUMERIC(18, 2) NOT NULL,
  exposure_notional NUMERIC(18, 2) NOT NULL DEFAULT 0,
  reserved_capital NUMERIC(18, 2) NOT NULL DEFAULT 0,
  currency VARCHAR(10) NOT NULL DEFAULT 'USD',
  status portfolio_status NOT NULL DEFAULT 'active',
  halt_reason TEXT,
  peak_equity NUMERIC(18, 2) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE experiments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  description TEXT,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  status experiment_status NOT NULL DEFAULT 'draft',
  start_date TIMESTAMPTZ NOT NULL,
  end_date TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE strategy_instances (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  strategy_version_id UUID NOT NULL REFERENCES strategy_versions(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  timeframe timeframe NOT NULL,
  risk_profile_id UUID NOT NULL REFERENCES risk_profiles(id),
  parameter_overrides JSONB,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  experiment_id UUID REFERENCES experiments(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX strategy_instances_active_unique
  ON strategy_instances (portfolio_id, strategy_version_id, instrument_id, timeframe)
  WHERE is_active = TRUE;

CREATE TABLE backtest_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  strategy_version_id UUID NOT NULL REFERENCES strategy_versions(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  timeframe timeframe NOT NULL,
  start_date TIMESTAMPTZ NOT NULL,
  end_date TIMESTAMPTZ NOT NULL,
  initial_capital NUMERIC(18, 2) NOT NULL,
  final_capital NUMERIC(18, 2),
  status backtest_status NOT NULL DEFAULT 'pending',
  parameters JSONB NOT NULL,
  execution_assumptions JSONB NOT NULL,
  dataset_fingerprint VARCHAR(64) NOT NULL,
  metrics JSONB,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX backtest_runs_strategy_instance_created_at_idx
  ON backtest_runs (strategy_instance_id, created_at DESC);

CREATE TABLE portfolio_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  timestamp TIMESTAMPTZ NOT NULL,
  balance NUMERIC(18, 2) NOT NULL,
  equity NUMERIC(18, 2) NOT NULL,
  exposure_notional NUMERIC(18, 2) NOT NULL,
  reserved_capital NUMERIC(18, 2) NOT NULL,
  unrealized_pnl NUMERIC(18, 2) NOT NULL,
  drawdown_pct NUMERIC(8, 4) NOT NULL,
  open_positions_count INT NOT NULL,
  metadata JSONB,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Trading flow tables
-- ---------------------------------------------------------------------------

CREATE TABLE signals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  strategy_version_id UUID NOT NULL REFERENCES strategy_versions(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  candle_timestamp TIMESTAMPTZ NOT NULL,
  action signal_action NOT NULL,
  reason TEXT NOT NULL,
  confidence NUMERIC(5, 4),
  suggested_sl NUMERIC(18, 8),
  suggested_tp NUMERIC(18, 8),
  metadata JSONB,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX signals_strategy_instance_candle_timestamp_idx
  ON signals (strategy_instance_id, candle_timestamp);

CREATE TABLE decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  signal_id UUID REFERENCES signals(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  candle_timestamp TIMESTAMPTZ NOT NULL,
  decision_type decision_type NOT NULL,
  message TEXT NOT NULL,
  metadata JSONB,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX decisions_strategy_instance_created_at_idx
  ON decisions (strategy_instance_id, created_at DESC);

CREATE INDEX decisions_created_at_idx
  ON decisions (created_at DESC);

CREATE TABLE order_intents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id UUID NOT NULL REFERENCES signals(id),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  direction direction NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  entry_type entry_type NOT NULL,
  limit_price NUMERIC(18, 8),
  stop_loss NUMERIC(18, 8) NOT NULL,
  take_profit NUMERIC(18, 8),
  target_risk_amount NUMERIC(18, 2) NOT NULL,
  actual_risk_amount NUMERIC(18, 2) NOT NULL,
  signal_candle_timestamp TIMESTAMPTZ NOT NULL,
  execution_candle_timestamp TIMESTAMPTZ,
  risk_profile_id UUID NOT NULL REFERENCES risk_profiles(id),
  status order_intent_status NOT NULL DEFAULT 'pending_execution',
  rejection_reason TEXT,
  idempotency_key VARCHAR(100) NOT NULL UNIQUE,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  intent_id UUID NOT NULL REFERENCES order_intents(id),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  direction direction NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  order_type order_type NOT NULL,
  status order_status NOT NULL DEFAULT 'pending',
  broker_order_id VARCHAR(100),
  submitted_at TIMESTAMPTZ,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  direction direction NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  entry_price NUMERIC(18, 8) NOT NULL,
  current_price NUMERIC(18, 8) NOT NULL,
  stop_loss NUMERIC(18, 8) NOT NULL,
  take_profit NUMERIC(18, 8),
  unrealized_pnl NUMERIC(18, 2) NOT NULL,
  status position_status NOT NULL DEFAULT 'open',
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX positions_portfolio_status_idx
  ON positions (portfolio_id, status);

CREATE TABLE fills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES orders(id),
  position_id UUID REFERENCES positions(id),
  fill_price NUMERIC(18, 8) NOT NULL,
  fill_quantity NUMERIC(18, 8) NOT NULL,
  fees NUMERIC(18, 4) NOT NULL DEFAULT 0,
  slippage NUMERIC(18, 8) NOT NULL DEFAULT 0,
  spread_cost NUMERIC(18, 4) NOT NULL DEFAULT 0,
  base_price NUMERIC(18, 8),
  side fill_side NOT NULL,
  filled_at TIMESTAMPTZ NOT NULL,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  position_id UUID NOT NULL REFERENCES positions(id),
  portfolio_id UUID NOT NULL REFERENCES portfolios(id),
  strategy_instance_id UUID NOT NULL REFERENCES strategy_instances(id),
  strategy_version_id UUID NOT NULL REFERENCES strategy_versions(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  direction direction NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  entry_price NUMERIC(18, 8) NOT NULL,
  exit_price NUMERIC(18, 8) NOT NULL,
  gross_pnl NUMERIC(18, 2) NOT NULL,
  realized_pnl NUMERIC(18, 2) NOT NULL,
  fees_total NUMERIC(18, 4) NOT NULL,
  slippage_total NUMERIC(18, 8) NOT NULL,
  spread_total NUMERIC(18, 4) NOT NULL,
  target_risk_amount NUMERIC(18, 2) NOT NULL,
  actual_risk_amount NUMERIC(18, 2) NOT NULL,
  exit_reason exit_reason NOT NULL,
  duration_seconds INT NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ NOT NULL,
  mode portfolio_mode NOT NULL,
  backtest_run_id UUID REFERENCES backtest_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX trades_strategy_version_closed_at_idx
  ON trades (strategy_version_id, closed_at DESC);

-- ---------------------------------------------------------------------------
-- Infrastructure tables
-- ---------------------------------------------------------------------------

CREATE TABLE market_data_providers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(50) NOT NULL,
  adapter_class VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  config JSONB,
  priority INT NOT NULL DEFAULT 0,
  last_success_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE worker_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type job_type NOT NULL,
  idempotency_key VARCHAR(200) NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  status worker_job_status NOT NULL DEFAULT 'pending',
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  scheduled_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE worker_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  worker_name VARCHAR(50) NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  status worker_run_status NOT NULL,
  jobs_processed INT NOT NULL DEFAULT 0,
  errors JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type VARCHAR(50) NOT NULL,
  entity_type VARCHAR(50) NOT NULL,
  entity_id UUID NOT NULL,
  payload JSONB,
  severity event_severity NOT NULL DEFAULT 'info',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key VARCHAR(100) NOT NULL UNIQUE,
  value JSONB NOT NULL,
  description TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Immutability rules (trades append-only)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION prevent_trades_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'trades table is append-only: UPDATE and DELETE are forbidden';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trades_no_update
  BEFORE UPDATE ON trades
  FOR EACH ROW EXECUTE FUNCTION prevent_trades_mutation();

CREATE TRIGGER trades_no_delete
  BEFORE DELETE ON trades
  FOR EACH ROW EXECUTE FUNCTION prevent_trades_mutation();

COMMIT;
