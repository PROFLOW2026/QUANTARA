-- Canonical Paper Broker layer (NETTING mode only at runtime).
-- Strategy legs are attribution-only; broker tables are execution truth.

BEGIN;

CREATE TYPE broker_account_state AS ENUM (
  'active',
  'margin_warning',
  'margin_call',
  'liquidation',
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
  cash NUMERIC(18, 2) NOT NULL,
  balance NUMERIC(18, 2) NOT NULL,
  equity NUMERIC(18, 2) NOT NULL,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  gross_exposure NUMERIC(18, 2) NOT NULL DEFAULT 0,
  net_exposure NUMERIC(18, 2) NOT NULL DEFAULT 0,
  initial_margin_used NUMERIC(18, 2) NOT NULL DEFAULT 0,
  maintenance_margin_required NUMERIC(18, 2) NOT NULL DEFAULT 0,
  free_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  available_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  spot_crypto_cash NUMERIC(18, 2) NOT NULL DEFAULT 0,
  account_state broker_account_state NOT NULL DEFAULT 'active',
  is_legacy_simulation BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT broker_accounts_cash_nonneg CHECK (cash >= 0),
  CONSTRAINT broker_accounts_balance_nonneg CHECK (balance >= 0)
);

COMMENT ON COLUMN broker_accounts.available_margin IS
  'Equity minus initial margin reserved — margin products buying capacity';
COMMENT ON COLUMN broker_accounts.spot_crypto_cash IS
  'Unallocated cash for spot crypto purchases (100% margin assets)';

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
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX broker_orders_idempotency_uq
  ON broker_orders (broker_account_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX broker_orders_account_created_idx ON broker_orders (broker_account_id, created_at DESC);
CREATE INDEX broker_orders_status_idx ON broker_orders (status);
CREATE INDEX broker_orders_intent_idx ON broker_orders (strategy_intent_id);

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

CREATE INDEX broker_fills_order_idx ON broker_fills (broker_order_id);
CREATE INDEX broker_fills_filled_at_idx ON broker_fills (filled_at DESC);

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

CREATE INDEX broker_attribution_fill_idx ON broker_attribution_ledger (broker_fill_id);
CREATE INDEX broker_attribution_portfolio_idx ON broker_attribution_ledger (strategy_portfolio_id);

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

-- Seed canonical paper broker (inactive until owner reset — legacy run stays separate)
INSERT INTO broker_accounts (
  slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
  available_margin, free_margin, spot_crypto_cash
) VALUES (
  'quantara_paper_competition',
  'quantara_standard_paper',
  'netting',
  320000, 320000, 320000, 320000,
  320000, 320000, 320000
) ON CONFLICT (slug) DO NOTHING;

COMMIT;
