-- Market regime snapshots (structure + volatility dimensions)

CREATE TABLE IF NOT EXISTS market_regime_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instrument_id UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  asset TEXT NOT NULL,
  timeframe timeframe NOT NULL,
  candle_time TIMESTAMPTZ NOT NULL,
  structure_regime TEXT NOT NULL,
  volatility_regime TEXT NOT NULL,
  metrics JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (asset, timeframe, candle_time)
);

CREATE INDEX IF NOT EXISTS idx_market_regime_snapshots_asset_tf_time
  ON market_regime_snapshots (asset, timeframe, candle_time DESC);

CREATE INDEX IF NOT EXISTS idx_market_regime_snapshots_instrument_tf_time
  ON market_regime_snapshots (instrument_id, timeframe, candle_time DESC);
