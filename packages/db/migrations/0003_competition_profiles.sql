-- Competition risk profiles and strategy compatibility

BEGIN;

INSERT INTO risk_profiles (
  id, name, slug, risk_per_trade_pct, max_open_positions,
  max_total_exposure_pct, daily_loss_limit_pct, max_drawdown_pct,
  is_default, parameters
) VALUES
  (
    '00000000-0000-0000-0000-000000000031',
    'Very Conservative',
    'very_conservative',
    0.25, 1, 100, 1.0, 5, false,
    '{"volatility_check_enabled": false}'::jsonb
  ),
  (
    '00000000-0000-0000-0000-000000000035',
    'Very Aggressive',
    'very_aggressive',
    2.0, 3, 100, 5.0, 15, false,
    '{"volatility_check_enabled": false}'::jsonb
  )
ON CONFLICT (slug) DO NOTHING;

UPDATE risk_profiles
SET risk_per_trade_pct = 1.5, name = 'Aggressive'
WHERE slug = 'aggressive';

UPDATE strategy_versions sv
SET risk_profile_compatibility = ARRAY[
  'very_conservative', 'conservative', 'balanced', 'aggressive', 'very_aggressive'
]::risk_profile_slug[]
FROM strategies s
WHERE sv.strategy_id = s.id
  AND s.slug = 'gold-trend-pullback'
  AND sv.version = '1.0.0';

COMMIT;
