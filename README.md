# QUANTARA

> **QUANTARA DOCUMENTATION = FINAL BASELINE**

מערכת פרטית למסחר אלגוריתמי, מחקר ובדיקת אסטרטגיות.

## Repository

- **GitHub:** https://github.com/PROFLOW2026/QUANTARA.git
- **Branch:** `main`
- **Push:** owner approval required — no automatic push

## Deployment

| Component | Where |
|-----------|-------|
| Next.js Web | Vercel (connect repo → auto-deploy on push to `main`) |
| Python Engine + Workers | Local development |
| PostgreSQL | Docker (local) |

## מה זה QUANTARA?

```
REAL MARKET DATA + VIRTUAL MONEY + REAL STRATEGY LOGIC
+ PAPER TRADING + BACKTESTING + ANALYTICS
```

- **Instrument:** XAU/USD (Gold)
- **Modes:** Backtest + Paper Trading
- **Strategy:** Gold Trend Pullback v1.0.0

## Tech Stack

Next.js · TypeScript · Tailwind · Python · FastAPI · PostgreSQL · Drizzle · SQLAlchemy · APScheduler

## Local Development

### Prerequisites

- Node.js 20+
- Python 3.11+
- Docker Desktop

### 1. Environment

```bash
cp .env.example .env
# Edit .env if needed
```

### 2. Database

```bash
docker compose up -d postgres
```

### 3. Install & migrate

```bash
npm install
npm run db:migrate
npm run db:seed
```

### 4. Python engine

```bash
cd apps/engine
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn quantara_engine.main:app --reload --port 8000
```

### 5. Workers (separate terminal)

```bash
cd apps/engine
.venv\Scripts\activate
python -m quantara_workers.main
```

### 6. Web app

```bash
npm run dev:web
```

Open http://localhost:3000

Engine API: http://localhost:8000/docs

### Demo scenario

```bash
npm run demo
```

Loads sample Gold candles, runs backtest + paper pipeline smoke test.

## Documentation

All specs in `/docs` — start with [00_MASTER_CONTEXT](docs/00_MASTER_CONTEXT.md).

Development rules: [CURSOR_INSTRUCTIONS.md](CURSOR_INSTRUCTIONS.md)

## License

Private personal project.
