-- 0019: Multi-broker Live Sim execution parity — vendor accounts use realistic_broker_v1.

UPDATE broker_accounts
SET execution_model = 'realistic_broker_v1'::execution_model_version,
    updated_at = NOW()
WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
  AND broker_environment = 'SIMULATION'
  AND execution_model IS DISTINCT FROM 'realistic_broker_v1'::execution_model_version;
