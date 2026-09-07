# QUANTARA — Project Structure

## 1. Repository Layout

```
QUANTARA/
├── README.md
├── CURSOR_INSTRUCTIONS.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── docs/                          # Planning docs (source of truth)
│   ├── 00_MASTER_CONTEXT.md
│   └── ... (all planning docs)
│
├── apps/
│   ├── web/                       # Next.js Frontend
│   │   ├── app/                   # App Router pages
│   │   │   ├── (dashboard)/
│   │   │   │   ├── page.tsx       # Home / Today
│   │   │   │   ├── portfolio/
│   │   │   │   ├── positions/
│   │   │   │   ├── journal/
│   │   │   │   ├── decisions/
│   │   │   │   ├── strategies/
│   │   │   │   ├── backtests/
│   │   │   │   ├── experiments/
│   │   │   │   ├── analytics/
│   │   │   │   ├── market/
│   │   │   │   └── settings/
│   │   │   ├── layout.tsx
│   │   │   └── api/               # UI-only API routes (if needed)
│   │   ├── components/
│   │   │   ├── ui/                # shadcn components
│   │   │   ├── charts/
│   │   │   ├── trading/           # PriceDisplay, PnLDisplay, etc.
│   │   │   └── layout/            # Sidebar, Nav
│   │   ├── lib/
│   │   │   ├── api-client.ts      # Engine API client
│   │   │   └── utils.ts
│   │   ├── messages/
│   │   │   ├── he.json
│   │   │   └── en.json
│   │   ├── package.json
│   │   └── tailwind.config.ts
│   │
│   └── engine/                    # Python Trading Engine
│       ├── pyproject.toml
│       ├── main.py                # FastAPI app entry
│       ├── api/
│       │   ├── routes/
│       │   │   ├── instruments.py
│       │   │   ├── candles.py
│       │   │   ├── strategies.py
│       │   │   ├── portfolio.py
│       │   │   ├── backtests.py
│       │   │   ├── analytics.py
│       │   │   ├── decisions.py
│       │   │   └── workers.py
│       │   └── deps.py
│       ├── core/
│       │   ├── config.py
│       │   ├── clock.py           # RealClock, BacktestClock
│       │   └── exceptions.py
│       ├── market_data/
│       │   ├── provider.py        # Protocol
│       │   ├── service.py
│       │   ├── validation.py
│       │   └── adapters/
│       │       ├── mock.py
│       │       └── base.py
│       ├── strategies/
│       │   ├── base.py
│       │   ├── registry.py
│       │   └── gold_trend_pullback/
│       │       └── v1_0_0.py
│       ├── risk/
│       │   ├── engine.py
│       │   ├── sizing.py
│       │   └── profiles.py
│       ├── execution/
│       │   ├── broker.py          # BrokerAdapter protocol
│       │   ├── paper_broker.py
│       │   ├── simulated_broker.py
│       │   └── fill_calculator.py
│       ├── portfolio/
│       │   ├── service.py
│       │   ├── pnl.py
│       │   └── snapshots.py
│       ├── backtesting/
│       │   ├── runner.py
│       │   └── metrics.py
│       ├── analytics/
│       │   ├── service.py
│       │   └── metrics.py
│       ├── models/                # SQLAlchemy models
│       │   └── ...
│       └── db/
│           └── session.py
│
├── workers/
│   ├── main.py                    # Scheduler entry point
│   ├── jobs/
│   │   ├── fetch_data.py
│   │   ├── run_strategy.py
│   │   ├── check_sl_tp.py
│   │   ├── create_snapshot.py
│   │   ├── run_backtest.py
│   │   └── health_monitor.py
│   └── scheduler.py
│
├── packages/
│   └── db/                        # Shared DB schema
│       ├── schema/
│       │   ├── instruments.ts
│       │   ├── candles.ts
│       │   ├── strategies.ts
│       │   ├── trading.ts
│       │   ├── portfolio.ts
│       │   └── workers.ts
│       ├── migrations/
│       ├── drizzle.config.ts
│       └── package.json
│
├── scripts/
│   ├── seed.py                    # Seed instruments, profiles, strategy
│   └── sync-models.py             # Verify SQLAlchemy ↔ Drizzle sync
│
└── tests/
    ├── engine/
    │   ├── test_strategies/
    │   ├── test_risk/
    │   ├── test_backtest/
    │   └── test_paper/
    ├── workers/
    └── integration/
```

## 2. Module Boundaries

| Module | Location | Can Import |
|--------|----------|------------|
| UI | apps/web | api-client, types only |
| Engine API | apps/engine/api | all engine modules |
| Strategy | apps/engine/strategies | core, indicators only |
| Risk | apps/engine/risk | core, portfolio (read) |
| Execution | apps/engine/execution | core, portfolio |
| Portfolio | apps/engine/portfolio | core, models |
| Workers | workers/ | engine modules |
| DB Schema | packages/db | nothing (standalone) |

**Forbidden imports:**
- strategies → execution, risk, db
- web → engine Python code
- UI components → strategy logic

## 3. Schema Sync Strategy

```
Source of truth: packages/db/schema/ (Drizzle)

Flow:
  1. Edit Drizzle schema
  2. Run drizzle-kit generate → SQL migration
  3. Apply migration to PostgreSQL
  4. Update SQLAlchemy models in apps/engine/models/ to match
  5. Run scripts/sync-models.py to verify column parity
```

## 4. Environment Variables

```bash
# .env.example

# Database
DATABASE_URL=postgresql://quantara:quantara@localhost:5432/quantara

# Engine
ENGINE_HOST=0.0.0.0
ENGINE_PORT=8000
QUANTARA_API_KEY=your-local-api-key

# Web
NEXT_PUBLIC_ENGINE_URL=http://localhost:8000
NEXT_PUBLIC_API_KEY=your-local-api-key

# Market Data (provider-specific — set during implementation)
MARKET_DATA_PROVIDER=mock
MARKET_DATA_API_KEY=

# Settings
DEFAULT_TIMEZONE=Asia/Jerusalem
DEFAULT_INITIAL_CAPITAL=10000
```

## 5. Docker Compose (Conceptual)

```yaml
services:
  postgres:
    image: postgres:15
    volumes: [pgdata:/var/lib/postgresql/data]
    ports: ["5432:5432"]

  engine:
    build: ./apps/engine
    ports: ["8000:8000"]
    depends_on: [postgres]

  worker:
    build: ./workers
    depends_on: [postgres, engine]

  web:
    build: ./apps/web
    ports: ["3000:3000"]
    depends_on: [engine]
```

## 6. Naming Conventions

| Item | Convention | Example |
|------|------------|---------|
| DB tables | snake_case, plural | `strategy_versions` |
| Python modules | snake_case | `gold_trend_pullback` |
| Python classes | PascalCase | `GoldTrendPullbackV1` |
| TS components | PascalCase | `PriceDisplay.tsx` |
| API routes | kebab-case | `/api/v1/strategy-versions` |
| Strategy slug | kebab-case | `gold-trend-pullback` |
| Enum values | snake_case | `risk_denied` |

## 7. Cross-References

- Architecture: `02_SYSTEM_ARCHITECTURE.md`
- DB entities: `04_DATABASE_MODEL.md`
- Dev phases: `16_DEVELOPMENT_PHASES.md`
