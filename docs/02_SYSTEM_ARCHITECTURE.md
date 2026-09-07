# QUANTARA — System Architecture

## 1. Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        QUANTARA SYSTEM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    HTTP/REST     ┌──────────────────────────┐ │
│  │  Next.js UI  │ ◄──────────────► │  Python Trading Engine   │ │
│  │  (Frontend)  │                  │  (FastAPI)               │ │
│  └──────┬───────┘                  └───────────┬──────────────┘ │
│         │                                      │                │
│         │         ┌────────────────────────────┤                │
│         │         │                            │                │
│         ▼         ▼                            ▼                │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    PostgreSQL Database                       ││
│  └─────────────────────────────────────────────────────────────┘│
│         ▲                                      ▲                │
│         │                                      │                │
│  ┌──────┴───────┐                  ┌───────────┴──────────────┐ │
│  │ Drizzle ORM  │                  │  SQLAlchemy 2.0          │ │
│  │ (read-heavy) │                  │  (write-heavy engine)    │ │
│  └──────────────┘                  └──────────────────────────┘ │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              Background Workers (Python)                     ││
│  │  Scheduler → Data Fetch → Strategy → Risk → Paper Exec      ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Technology Stack — Final Decision

### 2.1 Frontend

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Framework | **Next.js 14+ (App Router)** | SSR, API routes for UI-only needs, TypeScript |
| Language | TypeScript | Type safety across UI |
| Styling | Tailwind CSS | Rapid, consistent UI |
| Components | shadcn/ui pattern | Reusable, accessible, professional |
| i18n | next-intl (prepared, Hebrew first) | Localization readiness |
| Charts | Lightweight Charts / Recharts | Equity curves, analytics |

### 2.2 Trading Engine

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | **Python 3.11+** | Quant ecosystem |
| API | **FastAPI** | Async, OpenAPI, fast dev |
| Libraries | NumPy, Pandas, TA-Lib (or pandas-ta) | Indicators, data processing |
| Validation | Pydantic v2 | Schema validation |

**למה Python ל-engine ולא Node?**

- Strategy logic, backtesting, indicators — ecosystem עשיר
- NumPy/Pandas — standard ב-quant
- Separation of concerns: UI ב-TypeScript, logic ב-Python

### 2.3 Database

| Component | Choice | Rationale |
|-----------|--------|-----------|
| DB | **PostgreSQL 15+** | ACID, JSONB, reliability |
| Migrations | SQL files (source of truth) | Shared between Python + TS |

### 2.4 ORM Decision: Drizzle vs Prisma

**החלטה: Drizzle (TypeScript) + SQLAlchemy (Python)**

| קriterion | Drizzle | Prisma |
|-----------|---------|--------|
| TypeScript integration | ✅ Native, lightweight | ✅ Good but heavier |
| SQL control | ✅ SQL-first | ⚠️ Abstracted |
| Python sharing | ⚠️ TS only | ⚠️ TS only |
| Bundle size | ✅ Small | ❌ Larger |
| Migration flexibility | ✅ SQL migrations | ✅ Good |
| Read patterns (UI) | ✅ Excellent | ✅ Good |
| Write patterns (Engine) | N/A — Python handles | N/A |

**אסטרategia:**

1. **Schema source of truth**: Drizzle schema in `packages/db/schema/`
2. **Migrations**: Drizzle Kit generates SQL → committed to `packages/db/migrations/`
3. **Python**: SQLAlchemy models mirror schema manually (documented sync in `15_PROJECT_STRUCTURE.md`)
4. **Next.js**: Drizzle for read queries (UI, dashboards)
5. **Python Engine**: SQLAlchemy for all writes (trades, signals, workers)

**למה לא Prisma?**

- Prisma Client גדול יותר — פחות רלוונטי ל-UI read-heavy
- Drizzle SQL-first מתאים יותר כש-Python engine הוא writer העיקרי
- שני consumers (TS + Python) — SQL migrations משותפים עדיפים על ORM magic

### 2.5 Workers

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Scheduler | APScheduler (Phase 1) | Simple, sufficient for single user |
| Future | Celery + Redis | If scaling needed |
| Idempotency | DB unique constraints + job keys | See `13_BACKGROUND_WORKERS.md` |

### 2.6 Communication

| Path | Protocol |
|------|----------|
| UI → Engine API | REST (JSON) |
| UI → DB (reads) | Drizzle direct (optional, for fast reads) |
| Workers → DB | SQLAlchemy |
| Workers → Engine | Internal Python imports (same codebase) |

## 3. Module Architecture

```
quantara/
├── apps/
│   ├── web/                 # Next.js frontend
│   └── engine/              # Python FastAPI + core logic
├── packages/
│   └── db/                  # Drizzle schema + migrations
├── workers/                 # Background job runners
└── docs/                    # Planning docs (this folder)
```

### 3.1 Layer Separation (Mandatory)

| Layer | Responsibility | Must NOT |
|-------|---------------|----------|
| **Market Data** | Fetch, validate, store candles | Know about strategies |
| **Strategy** | Generate signals from candles | Know about broker/mode |
| **Risk Engine** | Validate/resize/deny signals | Generate strategy logic |
| **Execution** | Convert intents to orders/fills | Decide strategy |
| **Portfolio** | Track balance, equity, P&L | UI concerns |
| **Analytics** | Compute metrics from trades | Modify trades |
| **UI** | Display, configure, trigger | Contain trading logic |
| **Workers** | Orchestrate pipeline | Business logic inline |

### 3.2 Abstractions (Mode Independence)

Strategy code receives these interfaces — **never** knows the mode:

```python
# Conceptual — not implementation yet

class Clock(Protocol):
    def now(self) -> datetime: ...
    def current_candle_time(self) -> datetime: ...

class MarketDataProvider(Protocol):
    def get_candles(self, instrument, timeframe, until) -> list[Candle]: ...

class BrokerAdapter(Protocol):
    def submit_order(self, intent: OrderIntent) -> Order: ...
    def get_open_positions(self) -> list[Position]: ...

class ExecutionAdapter(Protocol):
    def execute(self, intent: OrderIntent) -> ExecutionResult: ...
```

| Mode | Clock | MarketDataProvider | ExecutionAdapter |
|------|-------|-------------------|------------------|
| BACKTEST | Simulated (candle time) | Historical replay | SimulatedBroker |
| PAPER | Real | Live feed | PaperBrokerAdapter |
| LIVE-MANUAL | Real | Live feed | ManualApprovalAdapter |
| LIVE-AUTOMATED | Real | Live feed | MT5BrokerAdapter |

## 4. API Design (Engine)

Base: `/api/v1`

| Domain | Endpoints (conceptual) |
|--------|----------------------|
| Instruments | GET /instruments, GET /instruments/{id} |
| Market Data | GET /candles, POST /market-data/refresh |
| Strategies | CRUD /strategies, /strategy-versions |
| Signals | GET /signals, GET /decisions |
| Portfolio | GET /portfolio, GET /positions, GET /trades |
| Backtests | POST /backtests, GET /backtests/{id} |
| Paper | GET /paper/status, POST /paper/start, POST /paper/stop |
| Analytics | GET /analytics/* |
| Settings | GET/PUT /settings |
| Workers | GET /workers/status |

UI reads primarily; writes go through Engine API for trading operations.

## 5. Authentication (Phase 1)

**Minimal single-user auth:**

- Simple session/API key for local deployment
- No registration flow
- Environment variable: `QUANTARA_API_KEY`
- Optional: basic Next.js middleware check
- **Not** building OAuth, MFA, password reset

Architecture must not hardcode "user_id = 1" in business logic — use `owner_id` field for future multi-user without rewrite.

## 6. Deployment Model

| Component | Runtime |
|-----------|---------|
| Next.js Web | **Vercel** — after owner connects `PROFLOW2026/QUANTARA`; auto-deploy on push to `main` |
| Python Engine / FastAPI | **Local dev** — future VPS for live paper |
| Workers / APScheduler | **Local dev** — future persistent host |
| PostgreSQL | **Local Docker** |

Vercel runs **Web only**. No Python workers or persistent FastAPI on Vercel.

Push/deploy only with owner approval.

## 7. Data Flow Summary

### 7.1 Paper Trading (Live Path)

```
Worker tick
  → Fetch latest candle (Market Data Adapter)
  → Validate & store candle
  → Load active strategy instances
  → For each instance:
      → Strategy.evaluate(candles, context) → Signal
      → Log Decision (always)
      → RiskEngine.evaluate(signal) → OrderIntent | DENIED
      → If approved: PaperBrokerAdapter.execute(intent)
      → Update Position / Portfolio
      → Check SL/TP on open positions
  → Create portfolio snapshot
  → Emit events
```

### 7.2 Backtest Path

```
User triggers backtest via UI
  → Engine creates BacktestRun record
  → Worker processes candle-by-candle (historical)
  → Same Strategy + Risk code as Paper
  → SimulatedBrokerAdapter with fees/spread/slippage
  → Store results + metrics
  → UI displays Backtest Detail
```

## 8. Event System

Lightweight event log in DB (not message queue in Phase 1):

| Event Type | Examples |
|------------|----------|
| market_data | candle_received, data_stale, provider_error |
| strategy | signal_generated, no_setup |
| risk | approved, denied, halted |
| execution | order_created, fill, sl_triggered, tp_triggered |
| portfolio | snapshot_created, drawdown_breach |
| system | worker_started, worker_error |

Events feed Decision Log and debugging.

## 9. Configuration

| Level | Storage |
|-------|---------|
| System settings | `settings` table + env vars |
| Strategy parameters | `strategy_versions.parameters` (JSONB) |
| Risk profile | `risk_profiles` table |
| Instrument config | `instruments` table |

Env vars for secrets only (API keys, DB URL).

## 10. Cross-Reference

| Topic | Document |
|-------|----------|
| Trading flow detail | `03_CANONICAL_TRADING_FLOW.md` |
| Database entities | `04_DATABASE_MODEL.md` |
| Project folders | `15_PROJECT_STRUCTURE.md` |
| Workers | `13_BACKGROUND_WORKERS.md` |
| Errors | `14_ERROR_AND_RECOVERY_RULES.md` |
