-- Equal per-asset capital allocation for Live Sim owner portfolio.

BEGIN;

ALTER TABLE owner_trading_portfolios
  ADD COLUMN IF NOT EXISTS equal_asset_allocation_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS owner_portfolio_asset_allocations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_portfolio_id UUID NOT NULL REFERENCES owner_trading_portfolios(id) ON DELETE CASCADE,
  canonical_symbol VARCHAR(32) NOT NULL,
  portfolio_broker_account_id UUID REFERENCES portfolio_broker_accounts(id) ON DELETE SET NULL,
  broker_account_id UUID REFERENCES broker_accounts(id) ON DELETE SET NULL,
  starting_allocated_capital NUMERIC(18, 2) NOT NULL,
  current_cash NUMERIC(18, 2) NOT NULL,
  realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
  fees_paid NUMERIC(18, 2) NOT NULL DEFAULT 0,
  funding_paid NUMERIC(18, 2) NOT NULL DEFAULT 0,
  gross_exposure NUMERIC(18, 2) NOT NULL DEFAULT 0,
  open_sl_risk_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
  trade_count INT NOT NULL DEFAULT 0,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  label_he VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT owner_asset_allocation_uq UNIQUE (owner_portfolio_id, canonical_symbol),
  CONSTRAINT owner_asset_starting_positive CHECK (starting_allocated_capital > 0),
  CONSTRAINT owner_asset_cash_nonneg CHECK (current_cash >= 0),
  CONSTRAINT owner_asset_trade_count_nonneg CHECK (trade_count >= 0)
);

CREATE INDEX owner_portfolio_asset_allocations_portfolio_idx
  ON owner_portfolio_asset_allocations (owner_portfolio_id, enabled);

CREATE INDEX owner_portfolio_asset_allocations_broker_idx
  ON owner_portfolio_asset_allocations (broker_account_id, enabled);

CREATE OR REPLACE FUNCTION enforce_asset_allocation_invariant()
RETURNS TRIGGER AS $$
DECLARE
  v_owner_id UUID;
  v_target NUMERIC(18, 2);
  v_sum NUMERIC(18, 2);
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_owner_id := OLD.owner_portfolio_id;
  ELSE
    v_owner_id := NEW.owner_portfolio_id;
  END IF;

  SELECT target_capital INTO v_target
  FROM owner_trading_portfolios
  WHERE id = v_owner_id
  FOR UPDATE;

  IF v_target IS NULL THEN
    RAISE EXCEPTION 'owner portfolio not found for asset allocation row';
  END IF;

  SELECT COALESCE(SUM(
    CASE
      WHEN TG_OP IN ('INSERT', 'UPDATE') AND a.id = NEW.id THEN 0
      WHEN a.enabled THEN a.starting_allocated_capital
      ELSE 0
    END
  ), 0)
  INTO v_sum
  FROM owner_portfolio_asset_allocations a
  WHERE a.owner_portfolio_id = v_owner_id;

  IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.enabled THEN
    v_sum := v_sum + NEW.starting_allocated_capital;
  END IF;

  IF v_sum > v_target THEN
    RAISE EXCEPTION USING
      MESSAGE = format(
        'enabled asset allocated capital (%s) exceeds owner target capital (%s)',
        v_sum, v_target
      );
  END IF;

  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    RETURN NEW;
  END IF;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_portfolio_asset_allocations_invariant
  BEFORE INSERT OR UPDATE OR DELETE ON owner_portfolio_asset_allocations
  FOR EACH ROW
  EXECUTE FUNCTION enforce_asset_allocation_invariant();

CREATE OR REPLACE FUNCTION enforce_equal_asset_activation()
RETURNS TRIGGER AS $$
DECLARE
  v_asset_sum NUMERIC(18, 2);
  v_asset_count INT;
BEGIN
  IF OLD.equal_asset_allocation_enabled IS TRUE
     OR NEW.equal_asset_allocation_enabled IS NOT TRUE THEN
    RETURN NEW;
  END IF;

  SELECT
    COALESCE(SUM(a.starting_allocated_capital) FILTER (WHERE a.enabled), 0),
    COUNT(*) FILTER (WHERE a.enabled)
  INTO v_asset_sum, v_asset_count
  FROM owner_portfolio_asset_allocations a
  WHERE a.owner_portfolio_id = NEW.id;

  IF v_asset_count < 2 THEN
    RAISE EXCEPTION 'equal asset activation blocked: requires at least two enabled asset allocations';
  END IF;

  IF v_asset_sum <> NEW.target_capital THEN
    RAISE EXCEPTION USING
      MESSAGE = format(
        'equal asset activation requires enabled asset allocations to equal target_capital exactly (%s != %s)',
        v_asset_sum, NEW.target_capital
      );
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_trading_portfolios_equal_asset_activation
  BEFORE UPDATE OF equal_asset_allocation_enabled ON owner_trading_portfolios
  FOR EACH ROW
  EXECUTE FUNCTION enforce_equal_asset_activation();

-- Seed owner-selected equal asset configuration (draft — multi-broker remains OFF).
DO $$
DECLARE
  v_owner_id UUID;
  v_ibkr_ba UUID;
  v_kraken_ba UUID;
  v_ibkr_link UUID;
  v_kraken_link UUID;
  v_per_asset NUMERIC(18, 2) := 1250;
BEGIN
  SELECT id INTO v_owner_id FROM owner_trading_portfolios WHERE slug = 'live-sim-owner';
  IF v_owner_id IS NULL THEN
    RETURN;
  END IF;

  INSERT INTO broker_accounts (
    slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
    spot_crypto_cash, is_active, pending_owner_reset, account_state,
    broker_vendor, broker_environment, connection_state,
    activated_at, risk_settings, account_metadata
  )
  SELECT
    'live-sim-ibkr-like', 'quantara_live_sim_10k', 'netting',
    0, 0, 0, 0, 0, FALSE, FALSE, 'paused',
    'IBKR', 'SIMULATION', 'DISCONNECTED',
    NULL, '{}'::jsonb,
    jsonb_build_object('label_he', 'IBKR (סימולציה)', 'owner_portfolio', 'live-sim-owner')
  WHERE NOT EXISTS (SELECT 1 FROM broker_accounts WHERE slug = 'live-sim-ibkr-like');

  INSERT INTO broker_accounts (
    slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
    spot_crypto_cash, is_active, pending_owner_reset, account_state,
    broker_vendor, broker_environment, connection_state,
    activated_at, risk_settings, account_metadata
  )
  SELECT
    'live-sim-kraken-like', 'quantara_live_sim_10k', 'netting',
    0, 0, 0, 0, 0, FALSE, FALSE, 'paused',
    'KRAKEN', 'SIMULATION', 'DISCONNECTED',
    NULL, '{}'::jsonb,
    jsonb_build_object('label_he', 'Kraken (סימולציה)', 'owner_portfolio', 'live-sim-owner')
  WHERE NOT EXISTS (SELECT 1 FROM broker_accounts WHERE slug = 'live-sim-kraken-like');

  SELECT id INTO v_ibkr_ba FROM broker_accounts WHERE slug = 'live-sim-ibkr-like';
  SELECT id INTO v_kraken_ba FROM broker_accounts WHERE slug = 'live-sim-kraken-like';

  INSERT INTO portfolio_broker_accounts (
    owner_portfolio_id, broker_account_id, allocated_capital, enabled, is_legacy_primary, label_he
  )
  SELECT v_owner_id, v_ibkr_ba, 7500, FALSE, FALSE, 'IBKR (סימולציה)'
  WHERE v_ibkr_ba IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM portfolio_broker_accounts
      WHERE owner_portfolio_id = v_owner_id AND broker_account_id = v_ibkr_ba
    );

  INSERT INTO portfolio_broker_accounts (
    owner_portfolio_id, broker_account_id, allocated_capital, enabled, is_legacy_primary, label_he
  )
  SELECT v_owner_id, v_kraken_ba, 2500, FALSE, FALSE, 'Kraken (סימולציה)'
  WHERE v_kraken_ba IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM portfolio_broker_accounts
      WHERE owner_portfolio_id = v_owner_id AND broker_account_id = v_kraken_ba
    );

  SELECT pba.id INTO v_ibkr_link
  FROM portfolio_broker_accounts pba
  WHERE pba.owner_portfolio_id = v_owner_id AND pba.broker_account_id = v_ibkr_ba;

  SELECT pba.id INTO v_kraken_link
  FROM portfolio_broker_accounts pba
  WHERE pba.owner_portfolio_id = v_owner_id AND pba.broker_account_id = v_kraken_ba;

  INSERT INTO owner_portfolio_asset_allocations (
    owner_portfolio_id, canonical_symbol, portfolio_broker_account_id, broker_account_id,
    starting_allocated_capital, current_cash, enabled, label_he
  )
  SELECT v_owner_id, sym, v_ibkr_link, v_ibkr_ba, v_per_asset, v_per_asset, FALSE, lbl
  FROM (VALUES
    ('NVDA', 'NVDA'),
    ('TSLA', 'TSLA'),
    ('AMD', 'AMD'),
    ('COIN', 'COIN'),
    ('XAUUSD', 'זהב'),
    ('GBPJPY', 'GBP/JPY')
  ) AS t(sym, lbl)
  WHERE NOT EXISTS (
    SELECT 1 FROM owner_portfolio_asset_allocations
    WHERE owner_portfolio_id = v_owner_id AND canonical_symbol = t.sym
  );

  INSERT INTO owner_portfolio_asset_allocations (
    owner_portfolio_id, canonical_symbol, portfolio_broker_account_id, broker_account_id,
    starting_allocated_capital, current_cash, enabled, label_he
  )
  SELECT v_owner_id, sym, v_kraken_link, v_kraken_ba, v_per_asset, v_per_asset, FALSE, lbl
  FROM (VALUES
    ('BTCUSD', 'Bitcoin'),
    ('ETHUSD', 'Ethereum')
  ) AS t(sym, lbl)
  WHERE NOT EXISTS (
    SELECT 1 FROM owner_portfolio_asset_allocations
    WHERE owner_portfolio_id = v_owner_id AND canonical_symbol = t.sym
  );

  UPDATE owner_trading_portfolios
  SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
        'equal_asset_allocation', jsonb_build_object(
          'configured', TRUE,
          'per_asset_capital', 1250,
          'ibkr_total', 7500,
          'kraken_total', 2500
        )
      ),
      updated_at = NOW()
  WHERE id = v_owner_id;
END $$;

COMMIT;
