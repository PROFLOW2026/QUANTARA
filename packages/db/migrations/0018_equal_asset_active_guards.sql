-- Equal-asset Live Sim: protect active broker links and target_capital while mode is on.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_broker_link_immutable_when_equal_asset()
RETURNS TRIGGER AS $$
DECLARE
  v_owner_id UUID;
  v_asset_count INT;
BEGIN
  v_owner_id := CASE
    WHEN TG_OP = 'DELETE' THEN OLD.owner_portfolio_id
    ELSE COALESCE(NEW.owner_portfolio_id, OLD.owner_portfolio_id)
  END;

  IF NOT owner_equal_asset_mode_active(v_owner_id) THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.is_legacy_primary THEN
      RETURN OLD;
    END IF;

    SELECT COUNT(*)
    INTO v_asset_count
    FROM owner_portfolio_asset_allocations a
    WHERE a.portfolio_broker_account_id = OLD.id
      AND a.enabled = TRUE;

    IF v_asset_count > 0 THEN
      RAISE EXCEPTION 'cannot delete broker link while equal-asset mode owns enabled asset allocations';
    END IF;

    RETURN OLD;
  END IF;

  IF TG_OP = 'UPDATE' AND NOT OLD.is_legacy_primary THEN
    SELECT COUNT(*)
    INTO v_asset_count
    FROM owner_portfolio_asset_allocations a
    WHERE a.portfolio_broker_account_id = OLD.id
      AND a.enabled = TRUE;

    IF v_asset_count > 0 THEN
      IF NEW.enabled IS NOT TRUE THEN
        RAISE EXCEPTION 'cannot disable broker link while equal-asset mode owns enabled asset allocations';
      END IF;

      IF NEW.broker_account_id IS DISTINCT FROM OLD.broker_account_id THEN
        RAISE EXCEPTION 'cannot reassign broker link broker_account_id while equal-asset mode is active';
      END IF;

      IF NEW.owner_portfolio_id IS DISTINCT FROM OLD.owner_portfolio_id THEN
        RAISE EXCEPTION 'cannot reassign broker link owner_portfolio_id while equal-asset mode is active';
      END IF;
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER portfolio_broker_accounts_immutable_when_equal_asset
  BEFORE UPDATE OR DELETE ON portfolio_broker_accounts
  FOR EACH ROW
  EXECUTE FUNCTION enforce_broker_link_immutable_when_equal_asset();

CREATE OR REPLACE FUNCTION enforce_equal_asset_portfolio_flags()
RETURNS TRIGGER AS $$
DECLARE
  v_has_enabled_assets BOOLEAN;
  v_enabled_asset_sum NUMERIC(18, 2);
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM owner_portfolio_asset_allocations a
    WHERE a.owner_portfolio_id = NEW.id
      AND a.enabled = TRUE
  )
  INTO v_has_enabled_assets;

  IF NEW.equal_asset_allocation_enabled IS TRUE
     AND NEW.multi_broker_mode_enabled IS NOT TRUE THEN
    RAISE EXCEPTION 'equal_asset_allocation_enabled requires multi_broker_mode_enabled';
  END IF;

  IF v_has_enabled_assets AND NEW.multi_broker_mode_enabled IS TRUE
     AND NEW.equal_asset_allocation_enabled IS NOT TRUE THEN
    RAISE EXCEPTION 'enabled asset allocations require equal_asset_allocation_enabled when multi_broker_mode_enabled';
  END IF;

  IF OLD.equal_asset_allocation_enabled IS TRUE
     AND NEW.equal_asset_allocation_enabled IS NOT TRUE
     AND NEW.multi_broker_mode_enabled IS TRUE THEN
    RAISE EXCEPTION 'cannot disable equal_asset_allocation_enabled while multi_broker_mode_enabled remains active';
  END IF;

  IF owner_equal_asset_mode_active(NEW.id)
     AND NEW.target_capital IS DISTINCT FROM OLD.target_capital THEN
    SELECT COALESCE(SUM(a.starting_allocated_capital), 0)
    INTO v_enabled_asset_sum
    FROM owner_portfolio_asset_allocations a
    WHERE a.owner_portfolio_id = NEW.id
      AND a.enabled = TRUE;

    IF NEW.target_capital IS DISTINCT FROM v_enabled_asset_sum THEN
      RAISE EXCEPTION USING
        MESSAGE = format(
          'target_capital (%s) must equal sum of enabled asset allocations (%s) while equal-asset mode is active',
          NEW.target_capital, v_enabled_asset_sum
        );
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS owner_trading_portfolios_equal_asset_flags ON owner_trading_portfolios;

CREATE TRIGGER owner_trading_portfolios_equal_asset_flags
  BEFORE UPDATE OF multi_broker_mode_enabled, equal_asset_allocation_enabled, target_capital
  ON owner_trading_portfolios
  FOR EACH ROW
  EXECUTE FUNCTION enforce_equal_asset_portfolio_flags();

COMMIT;
