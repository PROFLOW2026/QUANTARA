# QUANTARA Engine

Python trading engine for QUANTARA.

## Local run

### 1. Supabase setup

1. Create a Supabase project named **QUANTARA** (separate from ProjectFlow).
2. In Supabase → **Project Settings → Database**, copy the **Connection pooler** URI (port `6543`).
3. Copy `.env.example` to `.env` and set `DATABASE_URL` to local `quantara_prod` (see `docs/LOCAL-PRODUCTION-ARCHITECTURE.md`).
4. Optionally set `DIRECT_URL` for migrations/admin (port `5432`, direct host).

### 2. Apply schema (owner runs SQL manually)

In Supabase **SQL Editor**, run the full contents of:

`packages/db/migrations/0001_initial.sql`

Or from repo root (requires `DATABASE_URL` / `DIRECT_URL`):

```bash
npm run db:migrate
```

Verify ORM ↔ SQL parity:

```bash
python scripts/verify_schema_parity.py
```

### 3. Seed reference data

```bash
python scripts/seed.py
```

Creates XAUUSD instrument, risk profiles, gold-trend-pullback strategy v1.0.0, and default settings.

### 4. Run engine

```bash
cd apps/engine
pip install -e ".[dev]"
uvicorn main:app --reload
```

API docs: `http://localhost:8000/docs`

### 5. Tests

```bash
pytest
```

## Workers

```bash
python -m quantara_workers.main
```

Workers read/write through PostgreSQL via `TradingStore` (same as the API).
