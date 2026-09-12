-- Owner recovery plan when PARTIAL_TEST_RESIDUE is detected.
-- READ AND REVIEW ONLY — do not execute until Owner confirms no legitimate broker history exists.
-- Current known residue: __test_broker_integration__ account + 2 test orders/rejections.
-- quantara_paper_competition row is the intended placeholder (paused/pending_owner_reset).
--
-- NOT part of automated 0000→0007 migration chain. Owner runs manually before 0007.

BEGIN;

DO $$
DECLARE
  competition_orders int;
  competition_fills int;
  competition_positions int;
  competition_cash numeric;
  competition_balance numeric;
  competition_equity numeric;
  competition_active boolean;
  competition_pending_reset boolean;
  test_accounts int;
  test_orders int;
BEGIN
  SELECT COUNT(*) INTO competition_orders
  FROM broker_orders o
  JOIN broker_accounts a ON a.id = o.broker_account_id
  WHERE a.slug = 'quantara_paper_competition';

  SELECT COUNT(*) INTO competition_fills
  FROM broker_fills f
  JOIN broker_orders o ON o.id = f.broker_order_id
  JOIN broker_accounts a ON a.id = o.broker_account_id
  WHERE a.slug = 'quantara_paper_competition';

  SELECT COUNT(*) INTO competition_positions
  FROM broker_positions bp
  JOIN broker_accounts a ON a.id = bp.broker_account_id
  WHERE a.slug = 'quantara_paper_competition';

  SELECT cash, balance, equity, is_active, pending_owner_reset
  INTO competition_cash, competition_balance, competition_equity,
       competition_active, competition_pending_reset
  FROM broker_accounts
  WHERE slug = 'quantara_paper_competition';

  IF competition_orders > 0 OR competition_fills > 0 OR competition_positions > 0 THEN
    RAISE EXCEPTION
      'ABORT recovery: quantara_paper_competition has broker runtime history (orders=%, fills=%, positions=%). '
      'Recovery may only remove test residue — not legitimate owner broker history.',
      competition_orders, competition_fills, competition_positions;
  END IF;

  IF competition_cash IS NULL THEN
    RAISE EXCEPTION 'ABORT recovery: quantara_paper_competition placeholder missing';
  END IF;

  IF competition_cash <> 0 OR competition_balance <> 0 OR competition_equity <> 0 THEN
    RAISE EXCEPTION
      'ABORT recovery: quantara_paper_competition must be zeroed placeholder (cash=%, balance=%, equity=%)',
      competition_cash, competition_balance, competition_equity;
  END IF;

  IF competition_active IS TRUE THEN
    RAISE EXCEPTION 'ABORT recovery: quantara_paper_competition must be paused (is_active=FALSE)';
  END IF;

  IF competition_pending_reset IS NOT TRUE THEN
    RAISE EXCEPTION 'ABORT recovery: quantara_paper_competition must have pending_owner_reset=TRUE';
  END IF;

  SELECT COUNT(*) INTO test_accounts FROM broker_accounts WHERE slug = '__test_broker_integration__';
  SELECT COUNT(*) INTO test_orders
  FROM broker_orders o
  JOIN broker_accounts a ON a.id = o.broker_account_id
  WHERE a.slug = '__test_broker_integration__';

  IF test_accounts = 0 AND test_orders = 0 THEN
    RAISE NOTICE 'No __test_broker_integration__ residue — recovery is a no-op for test account';
  END IF;
END $$;

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
