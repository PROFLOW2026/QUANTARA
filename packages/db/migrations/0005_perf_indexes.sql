-- QUANTARA performance indexes for 160-portfolio batch queries (owner review — do not auto-apply)
-- Supports GROUP BY portfolio_id / instrument_id on paper trades and open positions.

CREATE INDEX IF NOT EXISTS trades_portfolio_paper_idx
  ON trades (portfolio_id)
  WHERE backtest_run_id IS NULL;

CREATE INDEX IF NOT EXISTS trades_instrument_paper_idx
  ON trades (instrument_id)
  WHERE backtest_run_id IS NULL;

CREATE INDEX IF NOT EXISTS trades_portfolio_closed_at_paper_idx
  ON trades (portfolio_id, closed_at DESC)
  WHERE backtest_run_id IS NULL;

CREATE INDEX IF NOT EXISTS portfolio_snapshots_portfolio_timestamp_idx
  ON portfolio_snapshots (portfolio_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS strategy_instances_experiment_active_idx
  ON strategy_instances (experiment_id, is_active)
  WHERE is_active = true;
