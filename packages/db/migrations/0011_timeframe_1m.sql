-- Position-only fast monitoring stores completed 1m OHLC bars separately from strategy cadence.
ALTER TYPE timeframe ADD VALUE IF NOT EXISTS '1m';
