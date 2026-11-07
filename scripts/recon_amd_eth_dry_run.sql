-- QUANTARA one-time AMD/ETH reconciliation — DRY RUN ONLY
-- Read-only SELECTs + proposed UPDATE text as comments.
-- Do NOT execute the UPDATE/INSERT blocks without explicit approval.
-- Idempotency key: recon_amd_eth_2026_11_07_v1

-- ========== SNAPSHOT (backup these result sets before any apply) ==========

-- AMD shadow
SELECT id, broker_account_id, direction, quantity, entry_price, stop_loss, take_profit,
       status, opened_at, closed_at, opportunity_key, strategy_slug
FROM live_sim_positions
WHERE id = '11154b64-ee49-402f-9bbc-31885631fec2';

-- AMD broker
SELECT ba.slug, bp.*
FROM broker_positions bp
JOIN broker_accounts ba ON ba.id = bp.broker_account_id
JOIN instruments i ON i.id = bp.instrument_id
WHERE ba.slug = 'live-sim-ibkr-like' AND i.symbol = 'AMD';

-- AMD lots on strategy position
SELECT id, strategy_position_id, direction, remaining_qty, entry_price, opportunity_key, opened_at
FROM broker_attribution_lots
WHERE strategy_position_id = '11154b64-ee49-402f-9bbc-31885631fec2';

-- ETH paper position
SELECT id, portfolio_id, direction, quantity, entry_price, stop_loss, take_profit, status, opened_at
FROM positions
WHERE id = '2ce6cba9-d2b4-4052-80a3-882689621a55';

-- ETH broker
SELECT ba.slug, bp.net_quantity, bp.average_price, bp.mark_price, bp.unrealized_pnl
FROM broker_positions bp
JOIN broker_accounts ba ON ba.id = bp.broker_account_id
JOIN instruments i ON i.id = bp.instrument_id
WHERE ba.slug = 'quantara_paper_competition' AND i.symbol = 'ETHUSD';

-- ETH attribution remaining
SELECT direction, remaining_qty, entry_price
FROM broker_attribution_lots
WHERE strategy_position_id = '2ce6cba9-d2b4-4052-80a3-882689621a55';

-- ========== PRECONDITIONS (must all be true before apply) ==========
-- AMD: same_dir_remaining = 0 AND shadow status = open
SELECT
  (SELECT COALESCE(SUM(remaining_qty),0)
     FROM broker_attribution_lots l
     JOIN live_sim_positions lsp ON lsp.id = l.strategy_position_id
    WHERE l.strategy_position_id = '11154b64-ee49-402f-9bbc-31885631fec2'
      AND lower(l.direction::text) = lower(lsp.direction::text)
      AND l.remaining_qty > 0) AS amd_same_dir_remaining,
  (SELECT status FROM live_sim_positions WHERE id = '11154b64-ee49-402f-9bbc-31885631fec2') AS amd_shadow_status,
  (SELECT net_quantity FROM broker_positions bp
     JOIN instruments i ON i.id = bp.instrument_id
     JOIN broker_accounts ba ON ba.id = bp.broker_account_id
    WHERE ba.slug = 'live-sim-ibkr-like' AND i.symbol = 'AMD') AS amd_broker_net;

-- ETH: positions.quantity != attribution remaining
SELECT
  p.quantity AS eth_positions_qty,
  (SELECT COALESCE(SUM(remaining_qty),0) FROM broker_attribution_lots
    WHERE strategy_position_id = p.id) AS eth_attr_remaining,
  bp.net_quantity AS eth_broker_net
FROM positions p
JOIN broker_accounts ba ON ba.slug = 'quantara_paper_competition'
JOIN instruments i ON i.symbol = 'ETHUSD'
LEFT JOIN broker_positions bp ON bp.broker_account_id = ba.id AND bp.instrument_id = i.id
WHERE p.id = '2ce6cba9-d2b4-4052-80a3-882689621a55';

-- ========== PROPOSED APPLY (commented — do not run in dry-run) ==========
-- Idempotent: WHERE status='open' / quantity mismatch guards.

-- AMD: close exhausted long shadow only. Does NOT flatten broker short -24.
-- Does NOT rewrite fills/ledger/realized_pnl.
/*
BEGIN;
UPDATE live_sim_positions
SET status = 'closed',
    closed_at = COALESCE(closed_at, NOW()),
    quantity = 0,
    updated_at = NOW()
WHERE id = '11154b64-ee49-402f-9bbc-31885631fec2'
  AND status = 'open'
  AND NOT EXISTS (
    SELECT 1 FROM broker_attribution_lots l
    WHERE l.strategy_position_id = live_sim_positions.id
      AND lower(l.direction::text) = lower(live_sim_positions.direction::text)
      AND l.remaining_qty > 0
  );

-- Detach opposite-direction flip orphans from the closed shadow (keep lots for audit).
UPDATE broker_attribution_lots
SET strategy_position_id = NULL,
    opportunity_key = NULL
WHERE strategy_position_id = '11154b64-ee49-402f-9bbc-31885631fec2'
  AND lower(direction::text) = 'short'
  AND remaining_qty > 0;

INSERT INTO settings (key, value) -- adjust table/name to project settings API if different
VALUES (
  'recon_amd_eth_2026_11_07_v1',
  '{"amd_shadow":"closed","eth_qty":"0.00285","applied_at":"PENDING"}'::jsonb
)
ON CONFLICT (key) DO NOTHING;
COMMIT;
*/

-- ETH: sync paper quantity to attribution remaining. No new broker order.
/*
BEGIN;
UPDATE positions
SET quantity = sub.rem,
    updated_at = NOW()
FROM (
  SELECT COALESCE(SUM(remaining_qty),0) AS rem
  FROM broker_attribution_lots
  WHERE strategy_position_id = '2ce6cba9-d2b4-4052-80a3-882689621a55'
) sub
WHERE id = '2ce6cba9-d2b4-4052-80a3-882689621a55'
  AND status = 'open'
  AND quantity <> sub.rem;
COMMIT;
*/

-- ========== POSTCHECKS ==========
-- AMD shadow status=closed, same_dir_remaining=0, broker_net unchanged (-24)
-- ETH positions.quantity = 0.00285 = broker abs net, status=open
-- No new broker_orders / broker_fills since apply timestamp
-- IBKR realized_pnl unchanged

-- ========== ROLLBACK ==========
-- Restore from snapshot CSV/SQL dump taken before apply.
-- AMD: UPDATE live_sim_positions SET status='open', quantity=30, closed_at=NULL, stop_loss=480.97451452 ...
--      re-attach short lot strategy_position_id if detached
-- ETH: UPDATE positions SET quantity=0.3447 WHERE id=...
-- Prefer restoring from snapshot over hand-written rollback values.
