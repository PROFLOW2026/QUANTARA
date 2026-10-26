-- Live Sim dynamic universe: slot rotation + new-entry flags (simulation only).

BEGIN;

ALTER TABLE owner_portfolio_asset_allocations
  ADD COLUMN IF NOT EXISTS live_sim_new_entries_allowed BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS live_sim_dynamic_universe_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_portfolio_id UUID NOT NULL REFERENCES owner_trading_portfolios(id) ON DELETE CASCADE,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  active_symbols JSONB NOT NULL,
  pinned_symbols JSONB NOT NULL,
  manage_only_symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
  rotated_slots JSONB NOT NULL DEFAULT '[]'::jsonb,
  scan_scores JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS live_sim_dynamic_universe_snapshots_owner_ts_idx
  ON live_sim_dynamic_universe_snapshots (owner_portfolio_id, generated_at DESC);

CREATE OR REPLACE FUNCTION live_sim_asset_slot_is_idle(p_owner UUID, p_sym TEXT)
RETURNS BOOLEAN AS $$
  SELECT NOT EXISTS (
    SELECT 1
    FROM live_sim_positions p
    JOIN instruments i ON i.id = p.instrument_id
    WHERE p.status = 'open'
      AND upper(replace(i.symbol, '/', '')) = upper(replace(p_sym, '/', ''))
      AND p.broker_account_id IN (
        SELECT a.broker_account_id
        FROM owner_portfolio_asset_allocations a
        WHERE a.owner_portfolio_id = p_owner
          AND upper(a.canonical_symbol) = upper(replace(p_sym, '/', ''))
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM owner_portfolio_asset_allocations a
    WHERE a.owner_portfolio_id = p_owner
      AND upper(a.canonical_symbol) = upper(replace(p_sym, '/', ''))
      AND (a.trade_count > 0 OR COALESCE(a.gross_exposure, 0) > 0)
  );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION enforce_asset_row_immutable_when_active()
RETURNS TRIGGER AS $$
BEGIN
  IF NOT owner_equal_asset_mode_active(
    CASE WHEN TG_OP = 'DELETE' THEN OLD.owner_portfolio_id ELSE NEW.owner_portfolio_id END
  ) THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'cannot delete asset allocation while equal-asset mode is active';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF NEW.enabled IS DISTINCT FROM OLD.enabled AND NEW.enabled IS NOT TRUE THEN
      RAISE EXCEPTION 'cannot disable asset allocation while equal-asset mode is active';
    END IF;

    IF NEW.starting_allocated_capital IS DISTINCT FROM OLD.starting_allocated_capital THEN
      RAISE EXCEPTION 'cannot change starting_allocated_capital while equal-asset mode is active';
    END IF;

    IF NEW.canonical_symbol IS DISTINCT FROM OLD.canonical_symbol THEN
      IF NOT live_sim_asset_slot_is_idle(OLD.owner_portfolio_id, OLD.canonical_symbol) THEN
        RAISE EXCEPTION 'cannot rotate asset symbol while slot has activity or open exposure';
      END IF;
    END IF;

    IF NEW.owner_portfolio_id IS DISTINCT FROM OLD.owner_portfolio_id THEN
      RAISE EXCEPTION 'cannot reassign asset allocation to another owner while equal-asset mode is active';
    END IF;

    IF NEW.portfolio_broker_account_id IS DISTINCT FROM OLD.portfolio_broker_account_id
       OR NEW.broker_account_id IS DISTINCT FROM OLD.broker_account_id THEN
      RAISE EXCEPTION 'cannot change asset broker routing while equal-asset mode is active';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
