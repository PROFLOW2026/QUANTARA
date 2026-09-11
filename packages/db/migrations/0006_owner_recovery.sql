-- Owner recovery plan when PARTIAL_TEST_RESIDUE is detected.
-- READ AND REVIEW ONLY — do not execute until Owner confirms no legitimate broker history exists.
-- Current known residue: __test_broker_integration__ account + 2 test orders/rejections.
-- quantara_paper_competition row is the intended placeholder (paused/pending_owner_reset).

BEGIN;

-- Remove integration-test broker account and all dependent rows (CASCADE via FK order).
DELETE FROM broker_order_rejections
WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__');

DELETE FROM broker_attribution_ledger
WHERE broker_fill_id IN (
  SELECT f.id FROM broker_fills f
  JOIN broker_orders o ON o.id = f.broker_order_id
  WHERE o.broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__')
);

DELETE FROM broker_attribution_lots
WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__');

DELETE FROM broker_fills
WHERE broker_order_id IN (
  SELECT id FROM broker_orders
  WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__')
);

DELETE FROM broker_orders
WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__');

DELETE FROM broker_positions
WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = '__test_broker_integration__');

DELETE FROM broker_accounts WHERE slug = '__test_broker_integration__';

-- Verify canonical placeholder remains exactly once.
-- SELECT slug, is_active, pending_owner_reset FROM broker_accounts;

COMMIT;

-- After successful recovery + verification, apply:
--   packages/db/migrations/0007_paper_competition_runs.sql
-- Then owner reset via scripts/paper_broker_reset.py --execute
