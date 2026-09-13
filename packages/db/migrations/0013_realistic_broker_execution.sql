-- Realistic broker execution model: products, client order IDs, protection, reconciliation.
-- Forward-only. Does not mutate historical trades/orders/fills.

BEGIN;

CREATE TYPE execution_product AS ENUM (
  'crypto_spot',
  'crypto_derivative',
  'margin_fx',
  'margin_gold',
  'equity_cash',
  'equity_margin_short'
);

CREATE TYPE execution_model_version AS ENUM (
  'legacy_spot_limited',
  'realistic_broker_v1'
);

CREATE TYPE time_in_force AS ENUM (
  'gtc',
  'day',
  'ioc',
  'fok'
);

CREATE TYPE protective_order_type AS ENUM (
  'stop_loss',
  'take_profit',
  'oco'
);

CREATE TYPE protective_order_status AS ENUM (
  'active',
  'triggered',
  'filled',
  'cancelled',
  'expired'
);

CREATE TYPE reconciliation_status AS ENUM (
  'ok',
  'warning',
  'halted'
);

ALTER TABLE paper_runs
  ADD COLUMN IF NOT EXISTS execution_model execution_model_version NOT NULL DEFAULT 'legacy_spot_limited',
  ADD COLUMN IF NOT EXISTS ended_reason VARCHAR(64);

ALTER TABLE broker_accounts
  ADD COLUMN IF NOT EXISTS execution_model execution_model_version NOT NULL DEFAULT 'legacy_spot_limited';

ALTER TABLE broker_orders
  ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(128),
  ADD COLUMN IF NOT EXISTS execution_product execution_product,
  ADD COLUMN IF NOT EXISTS execution_model execution_model_version,
  ADD COLUMN IF NOT EXISTS order_type VARCHAR(16) NOT NULL DEFAULT 'market',
  ADD COLUMN IF NOT EXISTS time_in_force time_in_force NOT NULL DEFAULT 'day',
  ADD COLUMN IF NOT EXISTS reduce_only BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS filled_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS remaining_quantity NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS submission_unknown BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS broker_orders_client_order_uq
  ON broker_orders (broker_account_id, client_order_id)
  WHERE client_order_id IS NOT NULL;

ALTER TABLE broker_positions
  ADD COLUMN IF NOT EXISTS execution_product execution_product,
  ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS accumulated_funding NUMERIC(18, 4) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS accumulated_borrow_fee NUMERIC(18, 4) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS instrument_execution_mappings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_symbol VARCHAR(32) NOT NULL UNIQUE,
  market_data_symbol VARCHAR(64),
  broker_symbol VARCHAR(64),
  broker_product_id VARCHAR(64),
  execution_product execution_product NOT NULL,
  venue VARCHAR(64),
  asset_class VARCHAR(32) NOT NULL,
  base_currency VARCHAR(16) NOT NULL,
  quote_currency VARCHAR(16) NOT NULL,
  contract_multiplier NUMERIC(18, 8) NOT NULL DEFAULT 1,
  tick_size NUMERIC(18, 8) NOT NULL DEFAULT 0.01,
  quantity_step NUMERIC(18, 8) NOT NULL DEFAULT 0.0001,
  min_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0.0001,
  min_notional NUMERIC(18, 2) NOT NULL DEFAULT 10,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS broker_protective_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  broker_position_id UUID NOT NULL REFERENCES broker_positions(id) ON DELETE CASCADE,
  broker_order_id UUID REFERENCES broker_orders(id),
  protective_type protective_order_type NOT NULL,
  trigger_price NUMERIC(18, 8) NOT NULL,
  status protective_order_status NOT NULL DEFAULT 'active',
  oco_sibling_id UUID REFERENCES broker_protective_orders(id),
  reduce_only BOOLEAN NOT NULL DEFAULT TRUE,
  client_order_id VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  triggered_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS broker_protective_orders_position_idx
  ON broker_protective_orders (broker_position_id, status);

CREATE TABLE IF NOT EXISTS broker_reconciliation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  status reconciliation_status NOT NULL DEFAULT 'ok',
  checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cash_delta NUMERIC(18, 4),
  equity_delta NUMERIC(18, 4),
  position_mismatches INT NOT NULL DEFAULT 0,
  order_mismatches INT NOT NULL DEFAULT 0,
  details JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS broker_reconciliation_account_idx
  ON broker_reconciliation_runs (broker_account_id, checked_at DESC);

-- Seed canonical instrument execution mappings (broker-agnostic).
INSERT INTO instrument_execution_mappings (
  canonical_symbol, market_data_symbol, broker_symbol, execution_product,
  asset_class, base_currency, quote_currency,
  contract_multiplier, tick_size, quantity_step, min_quantity, min_notional
) VALUES
  ('BTCUSD', 'BTC/USD', 'BTCUSD', 'crypto_spot', 'crypto', 'BTC', 'USD', 1, 0.01, 0.0001, 0.0001, 10),
  ('ETHUSD', 'ETH/USD', 'ETHUSD', 'crypto_spot', 'crypto', 'ETH', 'USD', 1, 0.01, 0.0001, 0.0001, 10),
  ('XAUUSD', 'XAU/USD', 'XAUUSD', 'margin_gold', 'commodity', 'XAU', 'USD', 1, 0.01, 0.01, 0.01, 10),
  ('GBPJPY', 'GBP/JPY', 'GBPJPY', 'margin_fx', 'forex', 'GBP', 'JPY', 1, 0.001, 1000, 1000, 1000),
  ('NVDA', 'NVDA', 'NVDA', 'equity_cash', 'stock', 'NVDA', 'USD', 1, 0.01, 1, 1, 1),
  ('TSLA', 'TSLA', 'TSLA', 'equity_cash', 'stock', 'TSLA', 'USD', 1, 0.01, 1, 1, 1),
  ('AMD', 'AMD', 'AMD', 'equity_cash', 'stock', 'AMD', 'USD', 1, 0.01, 1, 1, 1),
  ('COIN', 'COIN', 'COIN', 'equity_cash', 'stock', 'COIN', 'USD', 1, 0.01, 1, 1, 1)
ON CONFLICT (canonical_symbol) DO NOTHING;

COMMIT;
