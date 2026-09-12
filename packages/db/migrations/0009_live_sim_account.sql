-- Live Simulation $10K execution account + allocation audit trail.
-- Forward-only: existing broker history remains on quantara_paper_competition.

BEGIN;

ALTER TABLE broker_accounts
  ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS risk_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS account_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS live_sim_allocation_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  canonical_opportunity_key VARCHAR(512) NOT NULL,
  opportunity_key VARCHAR(512),
  strategy_slug VARCHAR(64) NOT NULL,
  strategy_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
  robot_label VARCHAR(64),
  symbol VARCHAR(20) NOT NULL,
  timeframe VARCHAR(16) NOT NULL,
  direction direction NOT NULL,
  signal_candle_timestamp TIMESTAMPTZ NOT NULL,
  proposed_entry NUMERIC(18, 8),
  stop_loss NUMERIC(18, 8),
  take_profit NUMERIC(18, 8),
  calculated_risk_usd NUMERIC(18, 2),
  calculated_quantity NUMERIC(18, 8),
  accepted BOOLEAN NOT NULL,
  rejection_reason VARCHAR(128),
  rejection_detail TEXT,
  resulting_open_sl_risk_usd NUMERIC(18, 2),
  symbol_sl_risk_pct NUMERIC(8, 4),
  group_sl_risk_pct NUMERIC(8, 4),
  group_name VARCHAR(64),
  broker_order_id UUID REFERENCES broker_orders(id),
  live_sim_position_id UUID,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS live_sim_allocation_canonical_uq
  ON live_sim_allocation_log (broker_account_id, canonical_opportunity_key);

CREATE INDEX IF NOT EXISTS live_sim_allocation_created_idx
  ON live_sim_allocation_log (broker_account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS live_sim_allocation_accepted_idx
  ON live_sim_allocation_log (broker_account_id, accepted, created_at DESC);

CREATE TABLE IF NOT EXISTS live_sim_positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  strategy_slug VARCHAR(64) NOT NULL,
  strategy_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
  robot_label VARCHAR(64),
  timeframe VARCHAR(16) NOT NULL,
  direction direction NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL CHECK (quantity > 0),
  entry_price NUMERIC(18, 8) NOT NULL CHECK (entry_price > 0),
  stop_loss NUMERIC(18, 8) NOT NULL CHECK (stop_loss > 0),
  take_profit NUMERIC(18, 8),
  current_price NUMERIC(18, 8),
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  planned_sl_risk_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  opportunity_key VARCHAR(512),
  canonical_opportunity_key VARCHAR(512) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ,
  allocation_log_id UUID REFERENCES live_sim_allocation_log(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT live_sim_positions_status_chk CHECK (status IN ('open', 'closed'))
);

CREATE INDEX IF NOT EXISTS live_sim_positions_open_idx
  ON live_sim_positions (broker_account_id, status, opened_at DESC);

CREATE INDEX IF NOT EXISTS live_sim_positions_symbol_idx
  ON live_sim_positions (broker_account_id, instrument_id, status);

-- Idempotent seed: $10K live simulation account (no historical trades).
INSERT INTO broker_accounts (
  slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
  spot_crypto_cash, is_active, pending_owner_reset, account_state,
  activated_at, risk_settings, account_metadata
) VALUES (
  'live-sim-10k',
  'quantara_live_sim_10k',
  'netting',
  10000, 10000, 10000, 10000,
  0,
  TRUE, FALSE, 'active',
  NOW(),
  jsonb_build_object(
    'risk_per_trade_pct', 1.00,
    'max_total_open_sl_risk_pct', 3.00,
    'max_symbol_sl_risk_pct', 1.00,
    'max_group_sl_risk_pct', 2.00,
    'daily_loss_gate_pct', 2.00,
    'max_drawdown_gate_pct', 10.00,
    'concentration_mode', 'ENFORCE',
    'high_water_mark', 10000,
    'daily_start_equity', 10000,
    'daily_start_date', to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD')
  ),
  jsonb_build_object('label_he', 'סימולציית $10,000', 'execution_layer', 'paper')
) ON CONFLICT (slug) DO NOTHING;

-- Ensure activated_at and balances for existing row (re-run safe).
UPDATE broker_accounts
SET
  activated_at = COALESCE(activated_at, NOW()),
  is_active = TRUE,
  pending_owner_reset = FALSE,
  account_state = 'active',
  risk_settings = CASE
    WHEN risk_settings = '{}'::jsonb OR risk_settings IS NULL THEN jsonb_build_object(
      'risk_per_trade_pct', 1.00,
      'max_total_open_sl_risk_pct', 3.00,
      'max_symbol_sl_risk_pct', 1.00,
      'max_group_sl_risk_pct', 2.00,
      'daily_loss_gate_pct', 2.00,
      'max_drawdown_gate_pct', 10.00,
      'concentration_mode', 'ENFORCE',
      'high_water_mark', COALESCE(equity, 10000),
      'daily_start_equity', COALESCE(equity, 10000),
      'daily_start_date', to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD')
    )
    ELSE risk_settings
  END
WHERE slug = 'live-sim-10k'
  AND activated_at IS NULL;

COMMIT;
