-- Equal-asset Live Sim: post-activation integrity, referential guards, flag coupling.

BEGIN;

-- current_cash = settled/available cash in the asset envelope (may go negative under losses).
-- Economic equity = current_cash + unrealized_pnl (not capped at zero).
ALTER TABLE owner_portfolio_asset_allocations
  DROP CONSTRAINT IF EXISTS owner_asset_cash_nonneg;

CREATE OR REPLACE FUNCTION owner_equal_asset_mode_active(p_owner_id UUID)
RETURNS BOOLEAN AS $$
  SELECT COALESCE(
    (
      SELECT equal_asset_allocation_enabled AND multi_broker_mode_enabled
      FROM owner_trading_portfolios
      WHERE id = p_owner_id
    ),
    FALSE
  );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION resolve_global_asset_routing_vendor(p_symbol TEXT)
RETURNS broker_vendor AS $$
DECLARE
  v_vendor broker_vendor;
BEGIN
  SELECT brr.broker_vendor
  INTO v_vendor
  FROM broker_routing_rules brr
  WHERE brr.enabled = TRUE
    AND brr.owner_portfolio_id IS NULL
    AND brr.canonical_symbol = upper(replace(p_symbol, '/', ''))
  ORDER BY brr.priority ASC, brr.created_at ASC
  LIMIT 1;

  IF v_vendor IS NULL THEN
    RAISE EXCEPTION 'no global routing rule for asset symbol %', p_symbol;
  END IF;

  RETURN v_vendor;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION enforce_asset_broker_referential()
RETURNS TRIGGER AS $$
DECLARE
  v_pba_owner UUID;
  v_pba_broker UUID;
  v_ba_vendor broker_vendor;
  v_route_vendor broker_vendor;
BEGIN
  IF NEW.portfolio_broker_account_id IS NOT NULL THEN
    SELECT pba.owner_portfolio_id, pba.broker_account_id
    INTO v_pba_owner, v_pba_broker
    FROM portfolio_broker_accounts pba
    WHERE pba.id = NEW.portfolio_broker_account_id;

    IF v_pba_owner IS NULL THEN
      RAISE EXCEPTION 'portfolio_broker_account_id does not exist';
    END IF;

    IF v_pba_owner <> NEW.owner_portfolio_id THEN
      RAISE EXCEPTION 'portfolio_broker_account_id belongs to a different owner portfolio';
    END IF;

    IF NEW.broker_account_id IS NOT NULL AND NEW.broker_account_id <> v_pba_broker THEN
      RAISE EXCEPTION 'broker_account_id does not match portfolio_broker_account link';
    END IF;

    IF NEW.broker_account_id IS NULL THEN
      NEW.broker_account_id := v_pba_broker;
    END IF;
  END IF;

  IF NEW.enabled OR owner_equal_asset_mode_active(NEW.owner_portfolio_id) THEN
    IF NEW.portfolio_broker_account_id IS NULL OR NEW.broker_account_id IS NULL THEN
      RAISE EXCEPTION 'enabled asset allocation requires portfolio_broker_account_id and broker_account_id';
    END IF;

    SELECT ba.broker_vendor
    INTO v_ba_vendor
    FROM broker_accounts ba
    WHERE ba.id = NEW.broker_account_id;

    IF v_ba_vendor IS NULL THEN
      RAISE EXCEPTION 'broker_account_id does not exist for asset allocation';
    END IF;

    v_route_vendor := resolve_global_asset_routing_vendor(NEW.canonical_symbol);

    IF v_ba_vendor <> v_route_vendor THEN
      RAISE EXCEPTION USING
        MESSAGE = format(
          'asset %s must route to broker vendor %s, not %s',
          NEW.canonical_symbol, v_route_vendor, v_ba_vendor
        );
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_portfolio_asset_allocations_broker_referential
  BEFORE INSERT OR UPDATE ON owner_portfolio_asset_allocations
  FOR EACH ROW
  EXECUTE FUNCTION enforce_asset_broker_referential();

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
      RAISE EXCEPTION 'cannot change canonical_symbol while equal-asset mode is active';
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

CREATE TRIGGER owner_portfolio_asset_allocations_immutable_when_active
  BEFORE UPDATE OR DELETE ON owner_portfolio_asset_allocations
  FOR EACH ROW
  EXECUTE FUNCTION enforce_asset_row_immutable_when_active();

CREATE OR REPLACE FUNCTION enforce_equal_asset_portfolio_flags()
RETURNS TRIGGER AS $$
DECLARE
  v_has_enabled_assets BOOLEAN;
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

  -- Broker-only multi-broker (no enabled per-asset rows) remains valid for future portfolios.
  IF v_has_enabled_assets AND NEW.multi_broker_mode_enabled IS TRUE
     AND NEW.equal_asset_allocation_enabled IS NOT TRUE THEN
    RAISE EXCEPTION 'enabled asset allocations require equal_asset_allocation_enabled when multi_broker_mode_enabled';
  END IF;

  IF OLD.equal_asset_allocation_enabled IS TRUE
     AND NEW.equal_asset_allocation_enabled IS NOT TRUE
     AND NEW.multi_broker_mode_enabled IS TRUE THEN
    RAISE EXCEPTION 'cannot disable equal_asset_allocation_enabled while multi_broker_mode_enabled remains active';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_trading_portfolios_equal_asset_flags
  BEFORE UPDATE OF multi_broker_mode_enabled, equal_asset_allocation_enabled
  ON owner_trading_portfolios
  FOR EACH ROW
  EXECUTE FUNCTION enforce_equal_asset_portfolio_flags();

CREATE OR REPLACE FUNCTION enforce_broker_allocated_cache_when_equal_asset()
RETURNS TRIGGER AS $$
DECLARE
  v_owner_id UUID;
  v_expected NUMERIC(18, 2);
BEGIN
  IF NEW.is_legacy_primary OR NOT NEW.enabled THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  SELECT pba.owner_portfolio_id INTO v_owner_id
  FROM portfolio_broker_accounts pba
  WHERE pba.id = NEW.id;

  IF NOT owner_equal_asset_mode_active(v_owner_id) THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  SELECT COALESCE(SUM(a.starting_allocated_capital), 0)
  INTO v_expected
  FROM owner_portfolio_asset_allocations a
  WHERE a.portfolio_broker_account_id = NEW.id
    AND a.enabled = TRUE;

  IF NEW.allocated_capital IS DISTINCT FROM v_expected THEN
    RAISE EXCEPTION USING
      MESSAGE = format(
        'broker allocated_capital (%s) must equal sum of enabled asset allocations (%s) while equal-asset mode is active',
        NEW.allocated_capital, v_expected
      );
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER portfolio_broker_accounts_equal_asset_cache
  BEFORE INSERT OR UPDATE OF allocated_capital, enabled
  ON portfolio_broker_accounts
  FOR EACH ROW
  EXECUTE FUNCTION enforce_broker_allocated_cache_when_equal_asset();

CREATE OR REPLACE FUNCTION check_equal_asset_portfolio_integrity()
RETURNS TRIGGER AS $$
DECLARE
  rec RECORD;
  v_pba RECORD;
  v_enabled_count INT;
  v_total_assets INT;
  v_distinct_start INT;
  v_per_asset NUMERIC(18, 2);
  v_sum NUMERIC(18, 2);
  v_expected_broker NUMERIC(18, 2);
BEGIN
  FOR rec IN
    SELECT id, target_capital
    FROM owner_trading_portfolios
    WHERE equal_asset_allocation_enabled IS TRUE
      AND multi_broker_mode_enabled IS TRUE
  LOOP
    SELECT
      COUNT(*) FILTER (WHERE a.enabled),
      COUNT(*),
      COUNT(DISTINCT a.starting_allocated_capital) FILTER (WHERE a.enabled),
      MIN(a.starting_allocated_capital) FILTER (WHERE a.enabled),
      COALESCE(SUM(a.starting_allocated_capital) FILTER (WHERE a.enabled), 0)
    INTO v_enabled_count, v_total_assets, v_distinct_start, v_per_asset, v_sum
    FROM owner_portfolio_asset_allocations a
    WHERE a.owner_portfolio_id = rec.id;

    IF v_enabled_count <> v_total_assets OR v_total_assets < 2 THEN
      RAISE EXCEPTION 'equal-asset mode requires every configured asset allocation to remain enabled';
    END IF;

    IF v_distinct_start <> 1 THEN
      RAISE EXCEPTION 'equal-asset mode requires uniform starting_allocated_capital across enabled assets';
    END IF;

    IF v_sum <> rec.target_capital THEN
      RAISE EXCEPTION USING
        MESSAGE = format(
          'equal-asset enabled total (%s) must equal target_capital (%s)',
          v_sum, rec.target_capital
        );
    END IF;

    IF v_per_asset * v_enabled_count <> rec.target_capital THEN
      RAISE EXCEPTION 'equal-asset mode requires each enabled asset to carry an equal share of target_capital';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM owner_portfolio_asset_allocations a
      WHERE a.owner_portfolio_id = rec.id
        AND a.enabled
        AND (
          a.portfolio_broker_account_id IS NULL
          OR a.broker_account_id IS NULL
        )
    ) THEN
      RAISE EXCEPTION 'equal-asset mode requires broker links on every enabled asset allocation';
    END IF;

    FOR v_pba IN
      SELECT link.id, link.allocated_capital
      FROM portfolio_broker_accounts link
      WHERE link.owner_portfolio_id = rec.id
        AND link.enabled = TRUE
        AND NOT link.is_legacy_primary
    LOOP
      SELECT COALESCE(SUM(a.starting_allocated_capital), 0)
      INTO v_expected_broker
      FROM owner_portfolio_asset_allocations a
      WHERE a.portfolio_broker_account_id = v_pba.id
        AND a.enabled = TRUE;

      IF v_pba.allocated_capital IS DISTINCT FROM v_expected_broker THEN
        RAISE EXCEPTION USING
          MESSAGE = format(
            'broker allocated_capital cache (%s) drifted from enabled asset sum (%s)',
            v_pba.allocated_capital, v_expected_broker
          );
      END IF;
    END LOOP;
  END LOOP;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER owner_portfolio_asset_allocations_equal_integrity
  AFTER INSERT OR UPDATE OR DELETE ON owner_portfolio_asset_allocations
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION check_equal_asset_portfolio_integrity();

CREATE CONSTRAINT TRIGGER portfolio_broker_accounts_equal_integrity
  AFTER INSERT OR UPDATE OR DELETE ON portfolio_broker_accounts
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION check_equal_asset_portfolio_integrity();

CREATE OR REPLACE FUNCTION enforce_equal_asset_activation()
RETURNS TRIGGER AS $$
DECLARE
  v_asset_sum NUMERIC(18, 2);
  v_asset_count INT;
  v_total_assets INT;
  v_distinct_start INT;
  v_per_asset NUMERIC(18, 2);
BEGIN
  IF OLD.equal_asset_allocation_enabled IS TRUE OR NEW.equal_asset_allocation_enabled IS NOT TRUE THEN
    RETURN NEW;
  END IF;

  IF NEW.multi_broker_mode_enabled IS NOT TRUE THEN
    RAISE EXCEPTION 'equal asset activation requires multi_broker_mode_enabled';
  END IF;

  SELECT
    COALESCE(SUM(a.starting_allocated_capital) FILTER (WHERE a.enabled), 0),
    COUNT(*) FILTER (WHERE a.enabled),
    COUNT(*),
    COUNT(DISTINCT a.starting_allocated_capital) FILTER (WHERE a.enabled),
    MIN(a.starting_allocated_capital) FILTER (WHERE a.enabled)
  INTO v_asset_sum, v_asset_count, v_total_assets, v_distinct_start, v_per_asset
  FROM owner_portfolio_asset_allocations a
  WHERE a.owner_portfolio_id = NEW.id;

  IF v_asset_count < 2 THEN
    RAISE EXCEPTION 'equal asset activation blocked: requires at least two enabled asset allocations';
  END IF;

  IF v_asset_count <> v_total_assets THEN
    RAISE EXCEPTION 'equal asset activation blocked: all configured asset allocations must be enabled';
  END IF;

  IF v_distinct_start <> 1 THEN
    RAISE EXCEPTION 'equal asset activation blocked: enabled assets must share one starting_allocated_capital';
  END IF;

  IF v_asset_sum <> NEW.target_capital THEN
    RAISE EXCEPTION USING
      MESSAGE = format(
        'equal asset activation requires enabled asset allocations to equal target_capital exactly (%s != %s)',
        v_asset_sum, NEW.target_capital
      );
  END IF;

  IF v_per_asset * v_asset_count <> NEW.target_capital THEN
    RAISE EXCEPTION 'equal asset activation blocked: assets must split target_capital equally';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
