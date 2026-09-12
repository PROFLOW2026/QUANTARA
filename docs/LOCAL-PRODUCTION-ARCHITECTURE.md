# QUANTARA Local Production Architecture

QUANTARA runtime no longer depends on Supabase. The canonical production database is **local PostgreSQL 17** on the Owner Windows machine.

## Components

| Layer | Host | Role |
|-------|------|------|
| **Web** | Vercel | Next.js dashboard; browser never touches PostgreSQL |
| **Engine API** | Owner PC | FastAPI (`apps/engine`); sole DB writer for trading |
| **Worker** | Owner PC | Scheduled jobs (market data, signals, PM) |
| **PostgreSQL** | Owner PC | Canonical DB `quantara_prod` — **localhost only** |
| **Cloudflare Tunnel** | Owner PC | Exposes Engine HTTP API only (port 8000) |
| **Supabase** | Cloud | **LEGACY ARCHIVE / NOT RUNTIME** — read-only historical reference |

```
Browser (Vercel) ──HTTPS──► Cloudflare Tunnel ──► Engine :8000 ──► PostgreSQL :5432 (localhost)
                                    │
                                    └── PostgreSQL port is NOT tunneled or public
```

## Databases

| Database | Purpose |
|----------|---------|
| `quantara_prod` | Owner production runtime — migrations, seeds, paper competition |
| `quantara_broker_test` | Disposable broker integration tests only |

Startup guardrails refuse:

- `DATABASE_URL` → `quantara_broker_test`
- `BROKER_TEST_DATABASE_URL` → `quantara_prod`
- Any Supabase hostname in runtime `DATABASE_URL`

## Environment

Copy `.env.example` → `.env` (gitignored). Required:

```env
DATABASE_URL=postgresql://quantara_prod:...@localhost:5432/quantara_prod
BROKER_TEST_DATABASE_URL=postgresql://quantara:quantara@localhost:5432/quantara_broker_test
```

Legacy Supabase URLs belong in `LEGACY_SUPABASE_*` variables for archive tooling only.

## Fresh production bootstrap

On an empty `quantara_prod`:

```powershell
npm run db:provision:local    # once — creates DB/user
npm run db:migrate            # 0001 → 0007
python scripts/seed.py
python scripts/seed_8_assets.py
python scripts/seed_competition.py
python scripts/seed_orb_strategy.py
python scripts/seed_orb_competition.py --activate
python scripts/paper_broker_reset.py --execute
python scripts/purge_mock_candles.py   # if mock rows ever present on quantara_prod
npm run db:bootstrap          # REAL provider history only (requires API keys in .env)
npm run report:market-data    # coverage + source audit table
npm run verify:db             # smoke test
```

**Never** set `MARKET_DATA_PROVIDER=mock` on `quantara_prod`. Mock is for tests / `quantara_broker_test` only.

Required `.env` keys for full 8-asset bootstrap:

- `MARKET_DATA_API_KEY` — Twelve Data (XAUUSD, GBPJPY)
- `ALPACA_API_KEY_ID` + `ALPACA_API_SECRET_KEY` — US equities (NVDA, TSLA, AMD, COIN)
- `TIINGO_API_KEY` — fallback chain (optional if primaries configured)
- BTC/ETH use Coinbase public API (no key)

Before first `START_QUANTARA.bat`:

1. Run `scripts/configure_postgres_localhost.ps1` **as Administrator**
2. Ensure `listen_addresses = 'localhost'` (START preflight verifies)
3. Confirm zero `source='mock'` rows: `python scripts/purge_mock_candles.py`

Do **not** run `packages/db/owner/0006_owner_recovery.sql` — that was Supabase residue recovery only.

## Owner start / stop

- **START**: `START_QUANTARA.bat` — preflight (PostgreSQL + schema), then Engine, Worker, Tunnel
- **STOP**: `STOP_QUANTARA.bat` — safe shutdown; does not expose PostgreSQL

If Windows service `postgresql-x64-17` is stopped, START shows a clear error.

## Backups

```powershell
npm run db:backup
# or: scripts/backup_local_db.ps1
```

Backups land in `backups/database/` (gitignored), pg_dump custom format, 14-day retention.

**Restore** (destructive — drops DB):

```powershell
scripts/restore_local_db.ps1 -BackupFile backups\database\quantara_prod_YYYYMMDD_HHMMSS.dump
python scripts/verify_local_runtime.py
```

## Future: cloud PostgreSQL / VPS

The application already uses generic PostgreSQL via `DATABASE_URL`. To move off the Owner PC:

1. Provision managed PostgreSQL (or VPS Postgres).
2. `pg_dump` / `pg_restore` from `quantara_prod`.
3. Point Owner `.env` `DATABASE_URL` at the new host (keep Engine + Worker on Owner or co-locate).
4. Keep Cloudflare Tunnel on Engine HTTP only — still do not expose Postgres publicly.

No application architecture change required; only connection string and network path.
