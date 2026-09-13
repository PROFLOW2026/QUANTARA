-- Durable simulated broker authority + protective quantity + cost accrual audit.
-- Forward-only. Does not mutate historical trades.

BEGIN;

CREATE TABLE IF NOT EXISTS sim_broker_authority_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  broker_order_id UUID NOT NULL REFERENCES broker_orders(id) ON DELETE CASCADE,
  client_order_id VARCHAR(128) NOT NULL,
  authoritative_status VARCHAR(32) NOT NULL,
  filled_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0,
  remaining_quantity NUMERIC(18, 8),
  submission_unknown BOOLEAN NOT NULL DEFAULT FALSE,
  accepted_at TIMESTAMPTZ,
  first_fill_at TIMESTAMPTZ,
  last_fill_at TIMESTAMPTZ,
  replace_of_order_id UUID REFERENCES broker_orders(id),
  payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (broker_account_id, client_order_id),
  UNIQUE (broker_order_id)
);

CREATE INDEX IF NOT EXISTS sim_broker_authority_account_status_idx
  ON sim_broker_authority_orders (broker_account_id, authoritative_status);

ALTER TABLE broker_protective_orders
  ADD COLUMN IF NOT EXISTS quantity NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS filled_quantity NUMERIC(18, 8) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS broker_cost_accrual_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_account_id UUID NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
  broker_position_id UUID REFERENCES broker_positions(id) ON DELETE SET NULL,
  cost_type VARCHAR(32) NOT NULL,
  amount NUMERIC(18, 4) NOT NULL,
  currency VARCHAR(8) NOT NULL DEFAULT 'USD',
  accrual_period_start TIMESTAMPTZ,
  accrual_period_end TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  details JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS broker_cost_accrual_account_idx
  ON broker_cost_accrual_log (broker_account_id, created_at DESC);

ALTER TABLE broker_accounts
  ADD COLUMN IF NOT EXISTS reconciliation_halted BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
