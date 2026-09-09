-- Opening Range Breakout (Robot B) strategy seed — apply via scripts/seed_orb_strategy.py
-- or run owner review before activating paper portfolios.

BEGIN;

INSERT INTO strategies (
  id, slug, name, description, supported_instruments,
  supported_timeframes, status
)
SELECT
  '00000000-0000-0000-0000-000000000310'::uuid,
  'opening-range-breakout',
  'Opening Range Breakout',
  'US equity opening range breakout on 5m candles — 30-minute range, ATR stop, 2R target, RTH only.',
  ARRAY(
    SELECT id FROM instruments WHERE symbol IN ('SPY', 'QQQ', 'NVDA', 'AAPL', 'MSFT')
  ),
  ARRAY['5m']::timeframe[],
  'active'
WHERE EXISTS (SELECT 1 FROM instruments WHERE symbol = 'SPY')
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  description = EXCLUDED.description,
  status = EXCLUDED.status;

INSERT INTO strategy_versions (
  id, strategy_id, version, version_major, version_minor, version_patch,
  parameters, parameters_schema, risk_profile_compatibility,
  logic_hash, changelog, is_active
)
SELECT
  '00000000-0000-0000-0000-000000000311'::uuid,
  s.id,
  '1.0.0',
  1, 0, 0,
  '{"opening_range_minutes":30,"timeframe":"5m","atr_period":14,"atr_sl_multiplier":1.5,"reward_risk_ratio":2.0,"stop_mode":"atr","entry_cutoff_et":"15:30","max_trades_per_day":1,"min_breakout_distance":0.0,"volume_confirmation":false,"retest_required":false,"min_candles_required":14}'::jsonb,
  '{"type":"object"}'::jsonb,
  ARRAY['very_conservative','conservative','balanced','aggressive','very_aggressive']::risk_profile_slug[],
  repeat('0', 64),
  'Initial release — ORB v1.0.0 US equities RTH',
  true
FROM strategies s
WHERE s.slug = 'opening-range-breakout'
ON CONFLICT (strategy_id, version) DO UPDATE SET
  parameters = EXCLUDED.parameters,
  is_active = true;

INSERT INTO settings (id, key, value, description)
VALUES
  (
    gen_random_uuid(),
    'orb_competition_enabled',
    'false'::jsonb,
    'ORB paper competition enabled'
  ),
  (
    gen_random_uuid(),
    'orb_competition_experiment_id',
    '"00000000-0000-0000-0000-000000000300"'::jsonb,
    'ORB experiment UUID'
  )
ON CONFLICT (key) DO NOTHING;

COMMIT;
