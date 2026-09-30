# Broker CI bootstrap @ 367122c (local repro)

**CI failure:** step `Bootstrap broker test schema` (~1s), run [36771402163](https://github.com/PROFLOW2026/QUANTARA/actions/runs/36771402163).

**Local repro (from repo root, match CI):**

```powershell
cd apps/engine
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5433/quantara_broker_test"
$env:DIRECT_URL=$env:DATABASE_URL
$env:BROKER_TEST_MIGRATE_URL=$env:DATABASE_URL
$env:BROKER_TEST_DATABASE_URL=$env:DATABASE_URL
$env:BROKER_TEST_ALLOW_MIGRATE_URL="1"
python tests/ci_bootstrap_broker.py
```

Requires Postgres on `127.0.0.1:5433` with DB `quantara_broker_test` (GitHub Actions service container).

**Likely causes to verify with traceback:**

1. `npm run db:migrate` failure (migration SQL / connection)
2. `broker_accounts` missing after migrate → `_broker_tables_exist` false
3. Import path when not run from `apps/engine`

**Fix stays on `v3-research` until full V3 release merge.**
