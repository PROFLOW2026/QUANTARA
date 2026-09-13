-- Repair pristine Live Sim accounts: spot_crypto_cash must mirror cash at startup.
-- Forward-only; does not modify migration 0009.

BEGIN;

-- Deterministic repair: zero-fill account with cash but no spot crypto buying power.
UPDATE broker_accounts ba
SET
  spot_crypto_cash = ba.cash,
  updated_at = NOW()
WHERE ba.slug = 'live-sim-10k'
  AND ba.spot_crypto_cash = 0
  AND ba.cash > 0
  AND ba.realized_pnl = 0
  AND ba.gross_realized_pnl = 0
  AND ba.fees_paid = 0
  AND NOT EXISTS (
    SELECT 1 FROM broker_orders bo WHERE bo.broker_account_id = ba.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM broker_fills bf
    JOIN broker_orders bo ON bo.id = bf.broker_order_id
    WHERE bo.broker_account_id = ba.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM broker_positions bp
    WHERE bp.broker_account_id = ba.id AND bp.net_quantity <> 0
  )
  AND NOT EXISTS (
    SELECT 1 FROM live_sim_positions lsp
    WHERE lsp.broker_account_id = ba.id
  );

-- Future owner resets / re-seeds: keep spot mirror aligned when account is reset to cash-only.
UPDATE broker_accounts ba
SET
  spot_crypto_cash = ba.cash,
  updated_at = NOW()
WHERE ba.profile_slug = 'quantara_live_sim_10k'
  AND ba.spot_crypto_cash <> ba.cash
  AND ba.cash = ba.starting_cash
  AND ba.realized_pnl = 0
  AND ba.gross_realized_pnl = 0
  AND ba.fees_paid = 0
  AND NOT EXISTS (
    SELECT 1 FROM broker_fills bf
    JOIN broker_orders bo ON bo.id = bf.broker_order_id
    WHERE bo.broker_account_id = ba.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM broker_positions bp
    WHERE bp.broker_account_id = ba.id AND bp.net_quantity <> 0
  )
  AND NOT EXISTS (
    SELECT 1 FROM live_sim_positions lsp
    WHERE lsp.broker_account_id = ba.id AND lsp.status = 'open'
  );

COMMIT;
