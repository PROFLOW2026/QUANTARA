-- Allow dynamic symbol rotation on flat slots (historical trade_count may remain > 0).

BEGIN;

CREATE OR REPLACE FUNCTION live_sim_asset_slot_is_idle(p_owner UUID, p_sym TEXT)
RETURNS BOOLEAN AS $$
  SELECT NOT EXISTS (
    SELECT 1
    FROM live_sim_positions p
    JOIN instruments i ON i.id = p.instrument_id
    WHERE p.status = 'open'
      AND upper(replace(i.symbol, '/', '')) = upper(replace(p_sym, '/', ''))
  )
  AND COALESCE(
    (
      SELECT a.gross_exposure
      FROM owner_portfolio_asset_allocations a
      WHERE a.owner_portfolio_id = p_owner
        AND upper(a.canonical_symbol) = upper(replace(p_sym, '/', ''))
      LIMIT 1
    ),
    0
  ) = 0;
$$ LANGUAGE sql STABLE;

COMMIT;
