# CURSOR_INSTRUCTIONS — QUANTARA

> **QUANTARA DOCUMENTATION = FINAL BASELINE**

> הוראות קבועות לכל session של פיתוח QUANTARA.
> קרא מסמך זה **לפני** כל משימת פיתוח.

## 1. Source of Truth

- **`/docs/*.md`** — baseline מחייב (FINAL BASELINE)
- **`CURSOR_INSTRUCTIONS.md`** — כללי עבודה (מסמך זה)
- **`README.md`** — הרצה מקומית + repo info

## 2. Project Context

QUANTARA = מערכת **פרטית** למסחר אלגוריתמי, מחקר, ובדיקת אסטרטגיות.

- **Scope:** BACKTEST + PAPER, XAU/USD, single user
- **לא SaaS** — אין productization

## 3. Repository & Git

| Item | Value |
|------|-------|
| Repository | https://github.com/PROFLOW2026/QUANTARA.git |
| Branch | `main` |
| Push | **רק** באישור מפורש מה-owner |
| Workflow | implement → test → integrate → report → owner approves → commit/push |

**אין push אוטומטי.**

## 4. Deployment

| Component | Where |
|-----------|-------|
| Next.js Web | **Vercel** — after owner connects repo; auto-deploy on approved push to `main` |
| Python Engine / FastAPI | **Local** (future: VPS) |
| Workers / APScheduler | **Local** (future: persistent host) |
| PostgreSQL | **Local Docker** |

Vercel runs **Web only** — not Python workers or persistent FastAPI.

## 5. Development Phases — NOT Approval Gates

Phases in `16_DEVELOPMENT_PHASES.md` = **build order / dependencies only**.

- **Do not stop** after Phase 1, 2, etc.
- **Continue** through all approved phases autonomously
- Main agent owns end-to-end completion
- Use parallel **AUTO agents** under main agent for non-conflicting work

Stop only on **blocking product decisions** not solvable from docs.

## 6. Absolute Rules — DO NOT

| # | Rule |
|---|------|
| 1 | אל תרחיב Scope |
| 2 | אל תבנה SaaS / MFA / Billing / Live money |
| 3 | אל תשנה Trading History בדיעבד |
| 4 | אל תיצור Lookahead Bias |
| 5 | אל תכתוב Strategy Logic ב-UI |
| 6 | אל תעשה Push/Deploy ללא אישור owner |
| 7 | אל תשמור קבצים מחוץ ל-QUANTARA |

## 7. Absolute Rules — DO

| # | Rule |
|---|------|
| 1 | אותו Strategy code ב-Backtest/Paper |
| 2 | Strategy change = Version חדש |
| 3 | Idempotency על writes |
| 4 | Log כל Decision |
| 5 | i18n keys — עברית ראשון |
| 6 | Analytics ב-Python — UI מציג בלבד |
| 7 | Phase 1 exposure ≤ 100% — no leverage engine |

## 8. P&L & Accounting (Canonical)

- **fill_price** includes spread + slippage
- **Gross LONG** = `(exit_fill - entry_fill) × qty`
- **Gross SHORT** = `(entry_fill - exit_fill) × qty`
- **Net** = gross - fees only
- **balance** = initial + cumulative net realized
- **equity** = balance + unrealized
- **Default execution:** signal candle N → fill at N+1 open (`next_open`)

## 9. Testing Philosophy — Private Project

Focus: P&L, balance/equity, sizing, spread/slippage, SL/TP, no-lookahead, next-open, parity, idempotency.

**Avoid:** endless QA, enterprise hardening, huge edge-case matrices.

**Prefer:** build → run → fix → continue.

## 10. Agent Strategy

- **Main agent:** orchestration, integration, conflicts, canonical behavior
- **AUTO agents:** parallel work on non-conflicting domains
- Only AUTO agents — no other agent types

## 11. When Uncertain

1. Check `/docs` first
2. Pick simple correct solution — don't stop for small technical choices
3. Update doc only if implementation requires clarification

## 12. Build Status

**Documentation:** FINAL BASELINE  
**Implementation:** IN PROGRESS — continuous full build through approved scope
