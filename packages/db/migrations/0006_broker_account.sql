-- Paper broker account layer (strategy legs separate from broker positions)

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
  buying_power NUMERIC(18, 2) NOT NULL DEFAULT 0,
  account_state broker_account_state NOT NULL DEFAULT 'active',
  is_legacy_simulation BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE broker_positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id),
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  net_quantity NUMERIC(18, 8) NOT NULL,
  average_price NUMERIC(18, 8) NOT NULL,
  mark_price NUMERIC(18, 8) NOT NULL,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  initial_margin NUMERIC(18, 2) NOT NULL DEFAULT 0,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (broker_account_id, instrument_id)
);

CREATE TABLE broker_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id),
  strategy_intent_id UUID,
  strategy_portfolio_id UUID,
  instrument_id UUID NOT NULL REFERENCES instruments(id),
  direction direction NOT NULL,
  requested_quantity NUMERIC(18, 8) NOT NULL,
  accepted_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0,
  status broker_order_status NOT NULL DEFAULT 'created',
  rejection_reason VARCHAR(64),
  rejection_detail TEXT,
  idempotency_key VARCHAR(256),
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broker_orders_account_created_idx ON broker_orders (broker_account_id, created_at DESC);
CREATE INDEX broker_orders_status_idx ON broker_orders (status);

CREATE TABLE broker_fills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_order_id UUID NOT NULL REFERENCES broker_orders(id),
  broker_position_id UUID REFERENCES broker_positions(id),
  fill_price NUMERIC(18, 8) NOT NULL,
  fill_quantity NUMERIC(18, 8) NOT NULL,
  fees NUMERIC(18, 4) NOT NULL DEFAULT 0,
  slippage NUMERIC(18, 8) NOT NULL DEFAULT 0,
  spread_cost NUMERIC(18, 4) NOT NULL DEFAULT 0,
  filled_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE broker_order_rejections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID REFERENCES broker_accounts(id),
  strategy_portfolio_id UUID,
  symbol VARCHAR(20) NOT NULL,
  quantity NUMERIC(18, 8) NOT NULL,
  reason VARCHAR(64) NOT NULL,
  detail TEXT,
  opportunity_key VARCHAR(512),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX broker_rejections_created_idx ON broker_order_rejections (created_at DESC);

-- Seed canonical paper broker account (inactive until owner reset)
INSERT INTO broker_accounts (
  slug, profile_slug, position_mode, starting_cash, cash, balance, equity, buying_power, free_margin
) VALUES (
  'quantara_paper_competition',
  'quantara_standard_paper',
  'netting',
  320000, 320000, 320000, 320000, 320000, 320000
) ON CONFLICT (slug) DO NOTHING;

COMMIT;
