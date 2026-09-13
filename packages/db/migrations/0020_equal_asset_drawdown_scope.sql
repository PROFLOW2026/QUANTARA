-- Per-asset drawdown scope for equal-asset Live Sim (asset equity vs asset HWM).

BEGIN;

ALTER TABLE owner_portfolio_asset_allocations
  ADD COLUMN IF NOT EXISTS high_water_mark NUMERIC(18, 2) NOT NULL DEFAULT 1250,
  ADD COLUMN IF NOT EXISTS daily_start_equity NUMERIC(18, 2) NOT NULL DEFAULT 1250,
  ADD COLUMN IF NOT EXISTS daily_start_date VARCHAR(10) NOT NULL DEFAULT '';

-- Zero Live Sim fills to date: align each asset HWM with its starting envelope.
UPDATE owner_portfolio_asset_allocations
SET high_water_mark = starting_allocated_capital,
    daily_start_equity = starting_allocated_capital,
    daily_start_date = COALESCE(
      NULLIF(daily_start_date, ''),
      to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD')
    );

-- Repair broker daily_start polluted by asset-scoped rolls (asset equity written to broker row).
UPDATE broker_accounts
SET risk_settings = (
      COALESCE(risk_settings, '{}'::jsonb)
      || jsonb_build_object(
        'daily_start_equity', starting_cash::float,
        'daily_start_date', to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
        'high_water_mark', GREATEST(
          starting_cash,
          COALESCE(NULLIF(risk_settings->>'high_water_mark', '')::numeric, starting_cash)
        )::float
      )
    ),
    updated_at = NOW()
WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like');

COMMIT;
