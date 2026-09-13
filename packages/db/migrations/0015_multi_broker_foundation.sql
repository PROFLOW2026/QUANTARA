-- Multi-broker foundation: owner portfolio, vendor identity, routing, fees, margin.
-- Forward-only. Preserves existing Research + Live Sim data.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

CREATE TYPE broker_vendor AS ENUM ('SIMULATED', 'IBKR', 'KRAKEN');

CREATE TYPE broker_environment AS ENUM ('SIMULATION', 'PAPER', 'LIVE');

CREATE TYPE broker_connection_state AS ENUM (
  'CONNECTED',
  'DEGRADED',
  'RECONCILIATION_REQUIRED',
  'HALTED',
  'DISCONNECTED'
);

CREATE TYPE owner_portfolio_status AS ENUM ('active', 'paused', 'archived');

-- ---------------------------------------------------------------------------
-- Owner trading portfolio (parent capital pool)
-- ---------------------------------------------------------------------------

CREATE TABLE owner_trading_portfolios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(128) NOT NULL,
  base_currency VARCHAR(10) NOT NULL DEFAULT 'USD',
  target_capital NUMERIC(18, 2) NOT NULL,
  multi_broker_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  global_execution_halted BOOLEAN NOT NULL DEFAULT FALSE,
  risk_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  status owner_portfolio_status NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT owner_portfolio_target_positive CHECK (target_capital > 0)
);

CREATE TABLE portfolio_broker_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_portfolio_id UUID NOT NULL REFERENCES owner_trading_portfolios(id) ON DELETE CASCADE,
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  allocated_capital NUMERIC(18, 2),
  allocation_pct NUMERIC(8, 4),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  is_legacy_primary BOOLEAN NOT NULL DEFAULT FALSE,
  label_he VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT portfolio_broker_account_uq UNIQUE (owner_portfolio_id, broker_account_id),
  CONSTRAINT portfolio_broker_account_single_uq UNIQUE (broker_account_id)
);

CREATE INDEX portfolio_broker_accounts_portfolio_idx
  ON portfolio_broker_accounts (owner_portfolio_id, enabled);

-- ---------------------------------------------------------------------------
-- Broker account vendor identity
-- ---------------------------------------------------------------------------

ALTER TABLE broker_accounts
  ADD COLUMN IF NOT EXISTS broker_vendor broker_vendor NOT NULL DEFAULT 'SIMULATED',
  ADD COLUMN IF NOT EXISTS broker_environment broker_environment NOT NULL DEFAULT 'SIMULATION',
  ADD COLUMN IF NOT EXISTS connection_state broker_connection_state NOT NULL DEFAULT 'CONNECTED';

-- Research paper broker
UPDATE broker_accounts
SET broker_vendor = 'SIMULATED',
    broker_environment = 'SIMULATION',
    connection_state = 'CONNECTED'
WHERE slug = 'quantara_paper_competition';

-- Live Sim (legacy single account until owner enables multi-broker)
UPDATE broker_accounts
SET broker_vendor = 'SIMULATED',
    broker_environment = 'SIMULATION',
    connection_state = 'CONNECTED'
WHERE slug = 'live-sim-10k';

-- ---------------------------------------------------------------------------
-- Instrument execution mappings — multi-broker
-- ---------------------------------------------------------------------------

ALTER TABLE instrument_execution_mappings
  ADD COLUMN IF NOT EXISTS broker_vendor broker_vendor NOT NULL DEFAULT 'SIMULATED',
  ADD COLUMN IF NOT EXISTS broker_account_id UUID REFERENCES broker_accounts(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS fractional_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS expiry_date DATE,
  ADD COLUMN IF NOT EXISTS contract_month VARCHAR(16),
  ADD COLUMN IF NOT EXISTS roll_date DATE,
  ADD COLUMN IF NOT EXISTS next_contract_symbol VARCHAR(64),
  ADD COLUMN IF NOT EXISTS product_type VARCHAR(32);

-- Drop single-canonical unique constraint
ALTER TABLE instrument_execution_mappings
  DROP CONSTRAINT IF EXISTS instrument_execution_mappings_canonical_symbol_key;

DROP INDEX IF EXISTS instrument_execution_mappings_canonical_symbol_key;

CREATE UNIQUE INDEX instrument_execution_mappings_scope_uq
  ON instrument_execution_mappings (
    canonical_symbol,
    broker_vendor,
    COALESCE(broker_account_id, '00000000-0000-0000-0000-000000000000'::uuid),
    execution_product
  );

CREATE INDEX instrument_execution_mappings_lookup_idx
  ON instrument_execution_mappings (canonical_symbol, broker_vendor, broker_account_id);

-- Backfill vendor on existing rows
UPDATE instrument_execution_mappings SET broker_vendor = 'SIMULATED' WHERE broker_vendor IS NULL;

-- IBKR-like simulation mappings (no real conids — metadata placeholders)
INSERT INTO instrument_execution_mappings (
  canonical_symbol, market_data_symbol, broker_symbol, broker_product_id,
  execution_product, broker_vendor, venue, asset_class, base_currency, quote_currency,
  contract_multiplier, tick_size, quantity_step, min_quantity, min_notional,
  fractional_allowed, product_type, metadata
) VALUES
  ('NVDA', 'NVDA', 'NVDA', NULL, 'equity_cash', 'IBKR', 'SMART', 'stock', 'NVDA', 'USD',
   1, 0.01, 1, 1, 1, FALSE, 'equity', '{"simulation_assumption": true}'::jsonb),
  ('TSLA', 'TSLA', 'TSLA', NULL, 'equity_cash', 'IBKR', 'SMART', 'stock', 'TSLA', 'USD',
   1, 0.01, 1, 1, 1, FALSE, 'equity', '{"simulation_assumption": true}'::jsonb),
  ('AMD', 'AMD', 'AMD', NULL, 'equity_cash', 'IBKR', 'SMART', 'stock', 'AMD', 'USD',
   1, 0.01, 1, 1, 1, FALSE, 'equity', '{"simulation_assumption": true}'::jsonb),
  ('COIN', 'COIN', 'COIN', NULL, 'equity_cash', 'IBKR', 'SMART', 'stock', 'COIN', 'USD',
   1, 0.01, 1, 1, 1, FALSE, 'equity', '{"simulation_assumption": true}'::jsonb),
  ('GBPJPY', 'GBP/JPY', 'GBPJPY', NULL, 'margin_fx', 'IBKR', 'IDEALPRO', 'forex', 'GBP', 'JPY',
   1, 0.001, 1000, 1000, 1000, FALSE, 'fx', '{"simulation_assumption": true}'::jsonb),
  ('XAUUSD', 'XAU/USD', 'XAUUSD', NULL, 'margin_gold', 'IBKR', 'SMART', 'commodity', 'XAU', 'USD',
   1, 0.01, 0.01, 0.01, 10, TRUE, 'gold_cfd',
   '{"simulation_assumption": true, "future_executable_alternatives": {"micro_futures": "MGC", "venue": "COMEX"}}'::jsonb)
ON CONFLICT DO NOTHING;

-- Kraken-like simulation mappings
INSERT INTO instrument_execution_mappings (
  canonical_symbol, market_data_symbol, broker_symbol, broker_product_id,
  execution_product, broker_vendor, venue, asset_class, base_currency, quote_currency,
  contract_multiplier, tick_size, quantity_step, min_quantity, min_notional,
  fractional_allowed, product_type, metadata
) VALUES
  ('BTCUSD', 'BTC/USD', 'PF_XBTUSD', 'PF_XBTUSD', 'crypto_derivative', 'KRAKEN', 'KRAKEN',
   'crypto', 'BTC', 'USD', 1, 0.5, 0.0001, 0.0001, 10, TRUE, 'perpetual',
   '{"simulation_assumption": true}'::jsonb),
  ('ETHUSD', 'ETH/USD', 'PF_ETHUSD', 'PF_ETHUSD', 'crypto_derivative', 'KRAKEN', 'KRAKEN',
   'crypto', 'ETH', 'USD', 1, 0.05, 0.001, 0.001, 10, TRUE, 'perpetual',
   '{"simulation_assumption": true}'::jsonb)
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Broker routing rules (configurable, not hardcoded)
-- ---------------------------------------------------------------------------

CREATE TABLE broker_routing_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_portfolio_id UUID REFERENCES owner_trading_portfolios(id) ON DELETE CASCADE,
  canonical_symbol VARCHAR(32) NOT NULL,
  direction direction,
  execution_product execution_product NOT NULL,
  broker_vendor broker_vendor NOT NULL,
  priority INT NOT NULL DEFAULT 100,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broker_routing_rules_lookup_idx
  ON broker_routing_rules (owner_portfolio_id, canonical_symbol, enabled, priority);

-- Default global routing (owner_portfolio_id NULL = applies when no portfolio override)
INSERT INTO broker_routing_rules (canonical_symbol, direction, execution_product, broker_vendor, priority) VALUES
  ('BTCUSD', 'long', 'crypto_derivative', 'KRAKEN', 10),
  ('BTCUSD', 'short', 'crypto_derivative', 'KRAKEN', 10),
  ('ETHUSD', 'long', 'crypto_derivative', 'KRAKEN', 10),
  ('ETHUSD', 'short', 'crypto_derivative', 'KRAKEN', 10),
  ('NVDA', 'long', 'equity_cash', 'IBKR', 10),
  ('NVDA', 'short', 'equity_margin_short', 'IBKR', 10),
  ('TSLA', 'long', 'equity_cash', 'IBKR', 10),
  ('TSLA', 'short', 'equity_margin_short', 'IBKR', 10),
  ('AMD', 'long', 'equity_cash', 'IBKR', 10),
  ('AMD', 'short', 'equity_margin_short', 'IBKR', 10),
  ('COIN', 'long', 'equity_cash', 'IBKR', 10),
  ('COIN', 'short', 'equity_margin_short', 'IBKR', 10),
  ('GBPJPY', 'long', 'margin_fx', 'IBKR', 10),
  ('GBPJPY', 'short', 'margin_fx', 'IBKR', 10),
  ('XAUUSD', 'long', 'margin_gold', 'IBKR', 10),
  ('XAUUSD', 'short', 'margin_gold', 'IBKR', 10);

-- ---------------------------------------------------------------------------
-- Broker-specific fee profiles (simulation defaults — not broker truth)
-- ---------------------------------------------------------------------------

CREATE TABLE broker_fee_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) NOT NULL UNIQUE,
  broker_vendor broker_vendor NOT NULL,
  execution_product execution_product,
  label_he VARCHAR(128),
  fee_model JSONB NOT NULL,
  simulation_assumption BOOLEAN NOT NULL DEFAULT TRUE,
  effective_from DATE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO broker_fee_profiles (slug, broker_vendor, execution_product, label_he, fee_model, metadata) VALUES
  ('kraken-derivatives-taker-v1', 'KRAKEN', 'crypto_derivative', 'קרaken — taker (סימולציה)',
   '{"fee_kind": "percentage_notional", "taker_rate": "0.0005", "maker_rate": "0.0002", "default_side": "taker"}'::jsonb,
   '{"source": "Kraken published tier 0 — SIMULATION ASSUMPTION", "dated": "2026-09"}'::jsonb),
  ('ibkr-equity-tiered-v1', 'IBKR', 'equity_cash', 'IBKR — מניות (סימולציה)',
   '{"fee_kind": "per_share", "per_share_rate": "0.0035", "minimum_per_order": "0.35"}'::jsonb,
   '{"source": "IBKR Pro tiered — SIMULATION ASSUMPTION", "dated": "2026-09"}'::jsonb),
  ('ibkr-equity-short-tiered-v1', 'IBKR', 'equity_margin_short', 'IBKR — short מניות (סימולציה)',
   '{"fee_kind": "per_share", "per_share_rate": "0.0035", "minimum_per_order": "0.35", "borrow_fee_annual_pct": "0.03"}'::jsonb,
   '{"source": "IBKR Pro tiered — SIMULATION ASSUMPTION", "dated": "2026-09"}'::jsonb),
  ('ibkr-fx-tiered-v1', 'IBKR', 'margin_fx', 'IBKR — FX (סימולציה)',
   '{"fee_kind": "bps_notional", "bps_rate": "0.20", "minimum_per_order": "2.00"}'::jsonb,
   '{"source": "IBKR spot FX tier I — SIMULATION ASSUMPTION", "dated": "2026-09"}'::jsonb),
  ('ibkr-gold-futures-v1', 'IBKR', 'margin_gold', 'IBKR — זהב (סימולציה)',
   '{"fee_kind": "per_contract", "per_contract_rate": "0.85", "minimum_per_order": "0.85"}'::jsonb,
   '{"source": "Placeholder per-contract — SIMULATION ASSUMPTION until product selected", "dated": "2026-09"}'::jsonb),
  ('simulated-generic-v1', 'SIMULATED', NULL, 'ברוקר סימולציה כללי',
   '{"fee_kind": "percentage_notional", "taker_rate": "0.0004"}'::jsonb,
   '{"note": "legacy single-account simulation"}'::jsonb);

-- ---------------------------------------------------------------------------
-- Broker-specific margin profiles
-- ---------------------------------------------------------------------------

CREATE TABLE broker_margin_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) NOT NULL UNIQUE,
  broker_vendor broker_vendor NOT NULL,
  execution_product execution_product NOT NULL,
  instrument_symbol VARCHAR(32),
  initial_margin_pct NUMERIC(8, 4) NOT NULL,
  maintenance_margin_pct NUMERIC(8, 4) NOT NULL,
  max_leverage NUMERIC(8, 4) NOT NULL,
  max_order_notional NUMERIC(18, 2),
  max_position_notional NUMERIC(18, 2),
  shorting_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  fractional_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  locate_required BOOLEAN NOT NULL DEFAULT FALSE,
  simulation_assumption BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO broker_margin_profiles (
  slug, broker_vendor, execution_product, initial_margin_pct, maintenance_margin_pct,
  max_leverage, max_order_notional, max_position_notional, shorting_allowed, fractional_allowed, locate_required
) VALUES
  ('ibkr-equity-cash-v1', 'IBKR', 'equity_cash', 50, 25, 2, 100000, 150000, FALSE, FALSE, FALSE),
  ('ibkr-equity-short-v1', 'IBKR', 'equity_margin_short', 50, 25, 2, 100000, 150000, TRUE, FALSE, TRUE),
  ('ibkr-fx-v1', 'IBKR', 'margin_fx', 5, 2.5, 20, 500000, 1000000, TRUE, FALSE, FALSE),
  ('ibkr-gold-v1', 'IBKR', 'margin_gold', 10, 5, 10, 200000, 400000, TRUE, TRUE, FALSE),
  ('kraken-crypto-deriv-v1', 'KRAKEN', 'crypto_derivative', 10, 5, 10, 100000, 200000, TRUE, TRUE, FALSE),
  ('kraken-crypto-spot-v1', 'KRAKEN', 'crypto_spot', 100, 100, 1, 50000, 100000, FALSE, TRUE, FALSE),
  ('simulated-crypto-deriv-v1', 'SIMULATED', 'crypto_derivative', 10, 5, 10, 100000, 200000, TRUE, TRUE, FALSE),
  ('simulated-equity-cash-v1', 'SIMULATED', 'equity_cash', 50, 25, 2, 100000, 150000, FALSE, FALSE, FALSE),
  ('simulated-equity-short-v1', 'SIMULATED', 'equity_margin_short', 50, 25, 2, 100000, 150000, TRUE, FALSE, TRUE),
  ('simulated-fx-v1', 'SIMULATED', 'margin_fx', 5, 2.5, 20, 500000, 1000000, TRUE, FALSE, FALSE),
  ('simulated-gold-v1', 'SIMULATED', 'margin_gold', 10, 5, 10, 200000, 400000, TRUE, TRUE, FALSE);

-- ---------------------------------------------------------------------------
-- Cross-venue price basis on fills
-- ---------------------------------------------------------------------------

ALTER TABLE broker_fills
  ADD COLUMN IF NOT EXISTS reference_price NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS execution_venue_price NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS venue_basis NUMERIC(18, 8);

-- ---------------------------------------------------------------------------
-- Futures roll tracking (primitives — no auto-roll yet)
-- ---------------------------------------------------------------------------

CREATE TABLE futures_contract_rolls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_symbol VARCHAR(32) NOT NULL,
  broker_vendor broker_vendor NOT NULL,
  from_contract_symbol VARCHAR(64) NOT NULL,
  to_contract_symbol VARCHAR(64) NOT NULL,
  roll_date DATE NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'planned',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT futures_contract_rolls_status_chk CHECK (status IN ('planned', 'executed', 'cancelled'))
);

CREATE INDEX futures_contract_rolls_symbol_idx
  ON futures_contract_rolls (canonical_symbol, broker_vendor, roll_date DESC);

-- ---------------------------------------------------------------------------
-- Seed Live Sim owner portfolio (legacy single-account mode)
-- ---------------------------------------------------------------------------

INSERT INTO owner_trading_portfolios (
  slug, name, base_currency, target_capital, multi_broker_mode_enabled, risk_settings, metadata
)
SELECT
  'live-sim-owner',
  'Live Sim Owner Portfolio',
  'USD',
  10000,
  FALSE,
  jsonb_build_object(
    'max_total_open_sl_risk_pct', 3.00,
    'max_symbol_sl_risk_pct', 1.00,
    'max_group_sl_risk_pct', 2.00,
    'daily_loss_gate_pct', 2.00,
    'max_drawdown_gate_pct', 10.00,
    'concentration_mode', 'ENFORCE'
  ),
  jsonb_build_object('label_he', 'תיק Live Sim', 'legacy_single_account', true)
WHERE NOT EXISTS (SELECT 1 FROM owner_trading_portfolios WHERE slug = 'live-sim-owner');

INSERT INTO portfolio_broker_accounts (
  owner_portfolio_id, broker_account_id, allocated_capital, enabled, is_legacy_primary, label_he
)
SELECT
  otp.id,
  ba.id,
  ba.starting_cash,
  TRUE,
  TRUE,
  'סימולציית $10,000'
FROM owner_trading_portfolios otp
CROSS JOIN broker_accounts ba
WHERE otp.slug = 'live-sim-owner'
  AND ba.slug = 'live-sim-10k'
  AND NOT EXISTS (
    SELECT 1 FROM portfolio_broker_accounts pba WHERE pba.broker_account_id = ba.id
  );

COMMIT;
