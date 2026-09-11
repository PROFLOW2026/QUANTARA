-- Canonical Paper Broker layer (NETTING mode only at runtime).
-- Strategy legs are attribution-only; broker tables are execution truth.
-- NO active account seeded — Owner must run approved reset before live paper.

BEGIN;

CREATE TYPE broker_account_state AS ENUM (
  'active',
  'margin_warning',
  'margin_call',
  'liquidation',
  'liquidation_pending',
  'paused'
);

CREATE TYPE broker_order_status AS ENUM (
  'created',
  'validating',
  'rejected',
  'accepted',
  'submitted',
  'partially_filled',
  'filled',
  'cancelled',
  'expired'
);

CREATE TYPE position_mode AS ENUM ('netting', 'hedging');

CREATE TABLE broker_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug VARCHAR(64) NOT NULL UNIQUE,
  profile_slug VARCHAR(64) NOT NULL,
  position_mode position_mode NOT NULL DEFAULT 'netting',
  account_currency VARCHAR(10) NOT NULL DEFAULT 'USD',
  starting_cash NUMERIC(18, 2) NOT NULL,
  cash NUMERIC(18, 2) NOT NULL DEFAULT 0,
  balance NUMERIC(18, 2) NOT NULL DEFAULT 0,
  equity NUMERIC(18, 2) NOT NULL DEFAULT 0,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  gross_exposure NUMERIC(18, 2) NOT NULL DEFAULT 0,
  net_exposure NUMERIC(18, 2) NOT NULL DEFAULT 0,
  initial_margin_used NUMERIC(18, 2) NOT NULL DEFAULT 0,
  maintenance_margin_required NUMERIC(18, 2) NOT NULL DEFAULT 0,
  free_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  available_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  spot_crypto_cash NUMERIC(18, 2) NOT NULL DEFAULT 0,
  account_state broker_account_state NOT NULL DEFAULT 'paused',
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  pending_owner_reset BOOLEAN NOT NULL DEFAULT TRUE,
  is_legacy_simulation BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE broker_positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  net_quantity NUMERIC(18, 8) NOT NULL,
  average_price NUMERIC(18, 8) NOT NULL,
  mark_price NUMERIC(18, 8) NOT NULL,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  initial_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  maintenance_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT broker_positions_qty_nonzero CHECK (net_quantity <> 0),
  CONSTRAINT broker_positions_avg_positive CHECK (average_price > 0),
  CONSTRAINT broker_positions_mark_positive CHECK (mark_price > 0),
  UNIQUE (broker_account_id, instrument_id)
);

CREATE TABLE broker_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  strategy_intent_id UUID,
  strategy_portfolio_id UUID,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  direction direction NOT NULL,
  requested_quantity NUMERIC(18, 8) NOT NULL CHECK (requested_quantity > 0),
  accepted_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0,
  status broker_order_status NOT NULL DEFAULT 'created',
  rejection_reason VARCHAR(64),
  rejection_detail TEXT,
  idempotency_key VARCHAR(256),
  order_purpose VARCHAR(32) DEFAULT 'entry',
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX broker_orders_idempotency_uq
  ON broker_orders (broker_account_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX broker_orders_account_created_idx ON broker_orders (broker_account_id, created_at DESC);
CREATE INDEX broker_orders_status_idx ON broker_orders (status);

CREATE TABLE broker_fills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_order_id UUID NOT NULL REFERENCES broker_orders(id) ON DELETE CASCADE,
  broker_position_id UUID REFERENCES broker_positions(id),
  fill_sequence SMALLINT NOT NULL DEFAULT 1,
  fill_price NUMERIC(18, 8) NOT NULL CHECK (fill_price > 0),
  fill_quantity NUMERIC(18, 8) NOT NULL CHECK (fill_quantity > 0),
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  fees NUMERIC(18, 4) NOT NULL DEFAULT 0,
  slippage NUMERIC(18, 8) NOT NULL DEFAULT 0,
  spread_cost NUMERIC(18, 4) NOT NULL DEFAULT 0,
  filled_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT broker_fills_order_sequence_uq UNIQUE (broker_order_id, fill_sequence)
);

CREATE TABLE broker_attribution_lots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  broker_fill_id UUID NOT NULL REFERENCES broker_fills(id) ON DELETE CASCADE,
  strategy_position_id UUID,
  strategy_portfolio_id UUID NOT NULL,
  symbol VARCHAR(20) NOT NULL,
  direction direction NOT NULL,
  remaining_qty NUMERIC(18, 8) NOT NULL CHECK (remaining_qty >= 0),
  entry_price NUMERIC(18, 8) NOT NULL,
  opportunity_key VARCHAR(512),
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX broker_attribution_lots_open_fill_uq
  ON broker_attribution_lots (broker_fill_id, strategy_portfolio_id, direction)
  WHERE remaining_qty > 0;

CREATE INDEX broker_attribution_lots_account_symbol_idx
  ON broker_attribution_lots (broker_account_id, symbol, opened_at);

CREATE TABLE broker_attribution_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_fill_id UUID NOT NULL REFERENCES broker_fills(id) ON DELETE CASCADE,
  strategy_position_id UUID,
  strategy_portfolio_id UUID NOT NULL,
  strategy_instance_id UUID,
  opportunity_key VARCHAR(512),
  quantity NUMERIC(18, 8) NOT NULL CHECK (quantity > 0),
  entry_price NUMERIC(18, 8),
  exit_price NUMERIC(18, 8),
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  direction direction NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX broker_attribution_ledger_close_uq
  ON broker_attribution_ledger (
    broker_fill_id, strategy_position_id, quantity, entry_price, exit_price, direction
  )
  WHERE exit_price IS NOT NULL;

CREATE TABLE broker_order_rejections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID REFERENCES broker_accounts(id),
  broker_order_id UUID REFERENCES broker_orders(id),
  strategy_portfolio_id UUID,
  symbol VARCHAR(20) NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  reason VARCHAR(64) NOT NULL,
  detail TEXT,
  opportunity_key VARCHAR(512),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broker_rejections_created_idx ON broker_order_rejections (created_at DESC);

-- Placeholder row: inactive until Owner-approved reset (NOT a live $320k account)
INSERT INTO broker_accounts (
  slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
  spot_crypto_cash, is_active, pending_owner_reset, account_state
) VALUES (
  'quantara_paper_competition',
  'quantara_standard_paper',
  'netting',
  320000, 0, 0, 0, 0,
  FALSE, TRUE, 'paused'
) ON CONFLICT (slug) DO NOTHING;

COMMIT;
