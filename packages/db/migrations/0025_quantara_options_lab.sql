-- QUANTARA Options Lab (isolated, additive, idempotent).
--
-- Creates ONLY new `opt_*` tables. Touches no existing table, enum, migration,
-- or equity repository. Safe to re-run on quantara_prod and quantara_broker_test.
--
-- Accounting conventions (enforced by apps/engine/quantara_options/db/repository.py):
--   * Received premium immediately increases opt_accounts.cash_balance,
--     premium_collected_gross and opt_strategies.open_liability_usd (cost-to-close).
--   * premium_realized_net / realized_pnl change ONLY when a strategy is
--     closed / expired / assigned / rolled (realized P&L is net of all fees).
--   * equity = cash_balance + long_options_market_value
--              - short_options_liability + stock_holdings_value

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(128) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'ai' CHECK (mode IN ('rules', 'ai')),
  strategy_scope VARCHAR(64),
  experiment_group VARCHAR(64) NOT NULL DEFAULT 'options_lab',
  starting_capital NUMERIC(18, 2) NOT NULL DEFAULT 0,
  cash_balance NUMERIC(18, 2) NOT NULL DEFAULT 0,
  cash_reserved NUMERIC(18, 2) NOT NULL DEFAULT 0,
  margin_used NUMERIC(18, 2) NOT NULL DEFAULT 0,
  long_options_market_value NUMERIC(18, 2) NOT NULL DEFAULT 0,
  short_options_liability NUMERIC(18, 2) NOT NULL DEFAULT 0,
  stock_holdings_value NUMERIC(18, 2) NOT NULL DEFAULT 0,
  equity NUMERIC(18, 2) NOT NULL DEFAULT 0,
  peak_equity NUMERIC(18, 2) NOT NULL DEFAULT 0,
  max_drawdown_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  max_drawdown_pct NUMERIC(8, 4) NOT NULL DEFAULT 0,
  premium_collected_gross NUMERIC(18, 2) NOT NULL DEFAULT 0,
  premium_realized_net NUMERIC(18, 2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  fees_paid NUMERIC(18, 2) NOT NULL DEFAULT 0,
  rule_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  wheel_states JSONB NOT NULL DEFAULT '{}'::jsonb,
  runtime_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Contract master + chain snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  occ_symbol VARCHAR(32) NOT NULL UNIQUE,
  underlying_symbol VARCHAR(16) NOT NULL,
  option_right VARCHAR(8) NOT NULL CHECK (option_right IN ('call', 'put')),
  strike NUMERIC(18, 4) NOT NULL,
  expiration_date DATE NOT NULL,
  multiplier INTEGER NOT NULL DEFAULT 100,
  exercise_style VARCHAR(16) NOT NULL DEFAULT 'american',
  root_symbol VARCHAR(16),
  tradable BOOLEAN NOT NULL DEFAULT TRUE,
  open_interest INTEGER NOT NULL DEFAULT 0,
  close_price NUMERIC(18, 4),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_contracts_underlying_exp
  ON opt_contracts (underlying_symbol, expiration_date);

CREATE TABLE IF NOT EXISTS opt_chain_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  underlying_symbol VARCHAR(16) NOT NULL,
  occ_symbol VARCHAR(32) NOT NULL,
  underlying_price NUMERIC(18, 4),
  bid NUMERIC(18, 4),
  ask NUMERIC(18, 4),
  mid NUMERIC(18, 4),
  last_trade NUMERIC(18, 4),
  volume INTEGER NOT NULL DEFAULT 0,
  open_interest INTEGER NOT NULL DEFAULT 0,
  implied_volatility DOUBLE PRECISION,
  delta DOUBLE PRECISION,
  gamma DOUBLE PRECISION,
  theta DOUBLE PRECISION,
  vega DOUBLE PRECISION,
  rho DOUBLE PRECISION,
  greeks_computed_locally BOOLEAN NOT NULL DEFAULT FALSE,
  feed_tier VARCHAR(32) NOT NULL DEFAULT 'alpaca_indicative',
  is_indicative BOOLEAN NOT NULL DEFAULT TRUE,
  is_delayed_trades BOOLEAN NOT NULL DEFAULT TRUE,
  is_stale BOOLEAN NOT NULL DEFAULT FALSE,
  spread_pct DOUBLE PRECISION,
  quote_timestamp TIMESTAMPTZ,
  snapshot_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  data_warnings JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_opt_chain_snap_underlying_ts
  ON opt_chain_snapshots (underlying_symbol, snapshot_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_opt_chain_snap_occ_ts
  ON opt_chain_snapshots (occ_symbol, snapshot_timestamp DESC);

-- ---------------------------------------------------------------------------
-- Strategies + legs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_strategies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES opt_accounts (id),
  account_slug VARCHAR(64) NOT NULL,
  strategy_type VARCHAR(64) NOT NULL,
  strategy_label_he VARCHAR(128) NOT NULL DEFAULT '',
  underlying_symbol VARCHAR(16) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'open',
  mode VARCHAR(16) NOT NULL DEFAULT 'ai',
  contracts_qty INTEGER NOT NULL DEFAULT 1,
  expiration_date DATE,
  is_credit BOOLEAN NOT NULL DEFAULT FALSE,
  net_entry_cash_flow NUMERIC(18, 2) NOT NULL DEFAULT 0,
  open_liability_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  long_value_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  collateral_locked_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  max_profit_usd NUMERIC(18, 2),
  max_loss_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  fees_paid NUMERIC(18, 2) NOT NULL DEFAULT 0,
  net_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
  net_theta DOUBLE PRECISION NOT NULL DEFAULT 0,
  net_vega DOUBLE PRECISION NOT NULL DEFAULT 0,
  pin_risk_warning BOOLEAN NOT NULL DEFAULT FALSE,
  early_assignment_warning BOOLEAN NOT NULL DEFAULT FALSE,
  wheel_state VARCHAR(32),
  ai_rationale TEXT,
  close_reason TEXT,
  payoff_curve JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_strategies_account_status
  ON opt_strategies (account_slug, status);
CREATE INDEX IF NOT EXISTS idx_opt_strategies_account_id
  ON opt_strategies (account_id);
CREATE INDEX IF NOT EXISTS idx_opt_strategies_expiration
  ON opt_strategies (expiration_date) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS opt_legs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id UUID NOT NULL REFERENCES opt_strategies (id) ON DELETE CASCADE,
  contract_id UUID REFERENCES opt_contracts (id),
  occ_symbol VARCHAR(32),
  underlying_symbol VARCHAR(16) NOT NULL,
  leg_role VARCHAR(32) NOT NULL,
  side VARCHAR(8) NOT NULL CHECK (side IN ('long', 'short')),
  option_right VARCHAR(8),
  strike NUMERIC(18, 4),
  expiration_date DATE,
  multiplier INTEGER NOT NULL DEFAULT 100,
  quantity INTEGER NOT NULL DEFAULT 1,
  entry_price NUMERIC(18, 4) NOT NULL DEFAULT 0,
  current_mark NUMERIC(18, 4) NOT NULL DEFAULT 0,
  bid NUMERIC(18, 4),
  ask NUMERIC(18, 4),
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  delta DOUBLE PRECISION,
  gamma DOUBLE PRECISION,
  theta DOUBLE PRECISION,
  vega DOUBLE PRECISION,
  implied_volatility DOUBLE PRECISION,
  status VARCHAR(24) NOT NULL DEFAULT 'open',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_legs_strategy ON opt_legs (strategy_id);
CREATE INDEX IF NOT EXISTS idx_opt_legs_occ ON opt_legs (occ_symbol);

-- ---------------------------------------------------------------------------
-- Orders / fills
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES opt_accounts (id),
  strategy_id UUID REFERENCES opt_strategies (id),
  action_kind VARCHAR(32) NOT NULL,
  legs_payload JSONB NOT NULL DEFAULT '[]'::jsonb,
  order_status VARCHAR(24) NOT NULL DEFAULT 'filled',
  idempotency_key VARCHAR(128) NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  filled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_opt_orders_account ON opt_orders (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_opt_orders_strategy ON opt_orders (strategy_id);

CREATE TABLE IF NOT EXISTS opt_fills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES opt_orders (id),
  strategy_id UUID NOT NULL REFERENCES opt_strategies (id),
  leg_id UUID NOT NULL REFERENCES opt_legs (id),
  occ_symbol VARCHAR(32),
  side VARCHAR(8) NOT NULL,
  quantity INTEGER NOT NULL,
  multiplier INTEGER NOT NULL DEFAULT 100,
  fill_price NUMERIC(18, 4) NOT NULL,
  bid_at_fill NUMERIC(18, 4),
  ask_at_fill NUMERIC(18, 4),
  slippage_usd NUMERIC(18, 4) NOT NULL DEFAULT 0,
  fee_usd NUMERIC(18, 4) NOT NULL DEFAULT 0,
  cash_flow_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  feed_tier VARCHAR(32) NOT NULL DEFAULT 'alpaca_indicative',
  is_indicative BOOLEAN NOT NULL DEFAULT TRUE,
  filled_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_fills_order ON opt_fills (order_id);
CREATE INDEX IF NOT EXISTS idx_opt_fills_strategy ON opt_fills (strategy_id);
CREATE INDEX IF NOT EXISTS idx_opt_fills_leg ON opt_fills (leg_id);

-- ---------------------------------------------------------------------------
-- Stock lots (wheel / covered call inventory)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_stock_lots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES opt_accounts (id),
  account_slug VARCHAR(64) NOT NULL,
  underlying_symbol VARCHAR(16) NOT NULL,
  shares INTEGER NOT NULL DEFAULT 0,
  avg_cost_basis NUMERIC(18, 4) NOT NULL DEFAULT 0,
  encumbered_shares INTEGER NOT NULL DEFAULT 0,
  source_strategy_id UUID,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (account_slug, underlying_symbol)
);

-- ---------------------------------------------------------------------------
-- Ledger / AI decisions / opportunities / engine state
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opt_ledger_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES opt_accounts (id),
  account_slug VARCHAR(64) NOT NULL,
  strategy_id UUID,
  event_type VARCHAR(32) NOT NULL,
  underlying_symbol VARCHAR(16) NOT NULL DEFAULT '',
  strategy_type VARCHAR(64) NOT NULL DEFAULT '',
  cash_delta NUMERIC(18, 2) NOT NULL DEFAULT 0,
  realized_pnl_delta NUMERIC(18, 2) NOT NULL DEFAULT 0,
  collateral_delta NUMERIC(18, 2) NOT NULL DEFAULT 0,
  fee_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_ledger_account_ts
  ON opt_ledger_events (account_slug, created_at DESC);

CREATE TABLE IF NOT EXISTS opt_ai_decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  decision_id VARCHAR(64) NOT NULL UNIQUE,
  account_slug VARCHAR(64) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'ai',
  action VARCHAR(16) NOT NULL,
  strategy_type VARCHAR(64),
  candidate_id VARCHAR(128),
  position_group_id VARCHAR(64),
  underlying_symbol VARCHAR(16) NOT NULL,
  contracts_qty INTEGER NOT NULL DEFAULT 1,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  decided_by VARCHAR(64) NOT NULL DEFAULT 'rules_engine',
  model_name VARCHAR(64),
  rationale_he TEXT NOT NULL DEFAULT '',
  rationale_en TEXT NOT NULL DEFAULT '',
  risk_guard_passed BOOLEAN NOT NULL DEFAULT FALSE,
  risk_guard_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  market_context JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_ai_decisions_account_ts
  ON opt_ai_decisions (account_slug, created_at DESC);

CREATE TABLE IF NOT EXISTS opt_opportunities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id VARCHAR(128) NOT NULL UNIQUE,
  strategy_type VARCHAR(64) NOT NULL,
  underlying_symbol VARCHAR(16) NOT NULL,
  expiration_date DATE NOT NULL,
  payload JSONB NOT NULL,
  rules_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  feed_tier VARCHAR(32) NOT NULL DEFAULT 'alpaca_indicative',
  is_indicative BOOLEAN NOT NULL DEFAULT TRUE,
  scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opt_opportunities_scanned
  ON opt_opportunities (scanned_at DESC);

CREATE TABLE IF NOT EXISTS opt_engine_state (
  id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  running_cycle BOOLEAN NOT NULL DEFAULT FALSE,
  mode VARCHAR(16) NOT NULL DEFAULT 'ai',
  feed_tier VARCHAR(32) NOT NULL DEFAULT 'alpaca_indicative',
  is_indicative BOOLEAN NOT NULL DEFAULT TRUE,
  is_delayed_trades BOOLEAN NOT NULL DEFAULT TRUE,
  market_open BOOLEAN NOT NULL DEFAULT FALSE,
  data_freshness_seconds DOUBLE PRECISION,
  last_cycle_at TIMESTAMPTZ,
  last_error TEXT,
  groq_available BOOLEAN NOT NULL DEFAULT FALSE,
  groq_model VARCHAR(64) NOT NULL DEFAULT 'openai/gpt-oss-120b',
  groq_calls_today INTEGER NOT NULL DEFAULT 0,
  groq_calls_date DATE,
  universe JSONB NOT NULL DEFAULT '["SPY","QQQ","IWM","AAPL","MSFT","NVDA","AMD","PLTR","SOFI","INTC"]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO opt_engine_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

ALTER TABLE opt_accounts
  ADD COLUMN IF NOT EXISTS runtime_meta JSONB NOT NULL DEFAULT '{}'::jsonb;
