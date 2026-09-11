-- Paper competition run / generation boundary (strategy research vs broker runtime).

BEGIN;

CREATE TABLE paper_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status VARCHAR(32) NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  starting_broker_cash NUMERIC(18, 2) NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX paper_runs_status_started_idx ON paper_runs (status, started_at DESC);

ALTER TABLE positions ADD COLUMN IF NOT EXISTS paper_run_id UUID REFERENCES paper_runs(id);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS paper_run_id UUID REFERENCES paper_runs(id);
ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS paper_run_id UUID REFERENCES paper_runs(id);

CREATE INDEX positions_paper_run_status_idx ON positions (paper_run_id, status);
CREATE INDEX trades_paper_run_portfolio_idx ON trades (paper_run_id, portfolio_id);
CREATE INDEX order_intents_paper_run_status_idx ON order_intents (paper_run_id, status);

COMMIT;
