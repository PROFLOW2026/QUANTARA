# QUANTARA — Development Phases

> **QUANTARA DOCUMENTATION = FINAL BASELINE**

## 1. Overview — Phases Are NOT Approval Gates

Phases describe **internal build order and dependencies only**.

| Rule | Meaning |
|------|---------|
| Phases = logical sequence | What depends on what |
| Phases ≠ owner approval gates | **Do not stop after Phase 1, 2, etc.** |
| Main agent owns completion | End-to-end build through all approved scope |
| Continuous execution | Plan → parallel AUTO agents → integrate → fix → continue → final QA |

**Correct workflow:**

```
Plan internally → parallel AUTO agents → integrate → continue → fix findings → final QA → final report
```

**Incorrect workflow:**

```
Phase 1 → stop → ask owner → Phase 2 → stop
```

Stop only on a **blocking product decision** that cannot be resolved from docs and materially changes trading behavior, money calculations, scope, or architecture.

## 2. Phase 0 — Planning ✅ COMPLETE

Documentation reviewed, corrected, and closed as **FINAL BASELINE**.

## 3. Phase 1 — Foundation & Database

| Task | Details |
|------|---------|
| Monorepo structure | Per `15_PROJECT_STRUCTURE.md` |
| Docker Compose + PostgreSQL | Local dev |
| Drizzle schema + migrations | `04_DATABASE_MODEL.md` |
| SQLAlchemy models | Mirror schema |
| Seed script | XAUUSD, risk profiles, strategy metadata |
| FastAPI skeleton | Health + API v1 |
| Next.js skeleton | Layout, nav, i18n |
| Git local config | Remote: `PROFLOW2026/QUANTARA`, branch `main` — **no push without owner** |

**Continue immediately to Phase 2** — no stop.

## 4. Phase 2 — Market Data Layer

Mock adapter, validation, candle storage, fetch worker, Market Data UI.

**Continue immediately to Phase 3.**

## 5. Phase 3 — Strategy Framework

BaseStrategy, registry, Gold Trend Pullback v1.0.0, unit tests.

**Continue immediately to Phase 4.**

## 6. Phase 4 — Risk Engine

Target/actual risk, sizing, exposure (max 100% Phase 1), halt logic.

**Continue immediately to Phase 5.**

## 7. Phase 5 — Paper Trading Engine

Full pipeline, next_open execution, canonical P&L, Home/Today UI.

**Continue immediately to Phase 6.**

## 8. Phase 6 — Backtesting

Candle loop, dataset fingerprint, metrics, Backtest UI.

**Continue immediately to Phase 7.**

## 9. Phase 7 — Analytics

Server-side metrics, Analytics UI, equity/drawdown charts.

**Continue immediately to Phase 8.**

## 10. Phase 8 — Polish & Integration

Error recovery, all remaining UI screens, integration tests, demo scenario, README run instructions.

## 11. Deployment Model (Reference)

| Component | Runtime |
|-----------|---------|
| Next.js Web | Vercel (after owner connects repo + approved push to `main`) |
| Python Engine / FastAPI | Local dev; future VPS for live paper |
| Workers / APScheduler | Local dev; future persistent host |
| PostgreSQL | Local Docker; production TBD |

Vercel does **not** run Python workers or persistent FastAPI. Do not assume otherwise.

## 12. Phase Summary

| Phase | Focus |
|-------|-------|
| 0 | Planning ✅ |
| 1 | Foundation + DB |
| 2 | Market Data |
| 3 | Strategy |
| 4 | Risk |
| 5 | Paper |
| 6 | Backtest |
| 7 | Analytics |
| 8 | Integration + QA |

**Total:** Execute continuously through Phase 8 in one build session.

## 13. Out of Scope (All Phases)

See `19_FUTURE_NOT_NOW.md` — Live money, SaaS, AI prediction, MT5 live, VPS setup, leverage >100%.
