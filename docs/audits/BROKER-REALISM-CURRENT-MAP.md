# Broker Realism — Current Architecture Map

Generated as part of the Real Broker Simulation Master Rebuild (2026-09-11).

## Purpose

Document where strategy logic, risk-to-SL sizing, capital checks, and execution currently live — and where virtual leverage bypasses broker realism.

## Layer Overview (Before Rebuild)

```
MARKET DATA → STRATEGY → SIGNAL → RISK ENGINE → OrderIntent → PaperBrokerAdapter → Fill → PortfolioState
```

**Missing layer:** canonical Paper Broker Account with buying power, margin, leverage, netting, and order rejection.

## Domain Objects

| Object | Location | Role |
|--------|----------|------|
| StrategyInstance | `models/portfolio.py` | Links portfolio + instrument + timeframe + risk profile |
| Portfolio | `models/portfolio.py` | Strategy allocation wallet ($2k each); treated as broker account today |
| Position | `models/trading.py` | Strategy leg (one per portfolio entry) |
| OrderIntent | `models/trading.py` | Pending execution request after risk approval |
| Order / Fill | `models/trading.py` | Execution artifacts tied to strategy portfolio |
| Trade | `models/trading.py` | Closed strategy leg P&L |
| Ledger | implicit via `Portfolio.balance` + trades | Realized P&L sync |

## Execution Flow

1. `fetch_data.py` — candles
2. `position_management.py` — SL/TP, marks
3. `run_strategy.py` → `CandleProcessor.process_candle`
4. Strategy → Signal → Decision log
5. `RiskEngine.evaluate` — sizing, open-risk guards, opportunity dedup
6. OrderIntent `PENDING_EXECUTION` (fill at N+1 candle)
7. `execute_intents.py` / in-process `_execute_pending`
8. `PaperBrokerAdapter.execute_entry` — always fills if reached
9. `PortfolioState.open_position_from_fill` — no balance deduction on entry

## Virtual Leverage Bypass Points

| File | Behavior |
|------|----------|
| `risk/engine.py:115,155-156,233` | `virtual_leverage = is_paper_competition_portfolio()` skips max exposure & drawdown; passes `allow_virtual_leverage=True` to sizing |
| `risk/sizing.py:223-248` | When `allow_virtual_leverage=True`, skips exposure headroom and `balance - reserved_capital` caps |
| `portfolio/service.py:77-79` | `reserved_capital = exposure_notional` but not enforced as buying power |
| `competition/leverage.py` | Computes display metric `virtual_leverage = notional/equity` |

## Risk Guards (Strategy Layer — Preserved)

| Guard | Limit | Scope |
|-------|-------|-------|
| Portfolio open risk | 10% | Per strategy portfolio equity |
| Per-portfolio asset risk | 6% | Per portfolio + instrument |
| Strategy+asset risk | 4% | Per instance + instrument |
| Global asset open risk | 2% | All 160 portfolios vs asset-allocated equity |

These constrain **risk-to-SL dollars**, not gross notional or margin.

## Exposure Calculation

- `persistence/batch_summary.py` — USD exposure via `resolve_dashboard_fx_rates`
- `portfolio/pnl.py` — `exposure_notional_account`
- JPY pairs: quote notional ÷ JPY/USD rate

## Market Data Dependencies

- `market_data/registry.py` — per-asset provider chains
- `market_data/provider_resolver.py` — failover, cooldown, budgets
- `market_data/tiingo_fallback_scheduler.py` — quota-aware scheduling

## Integration Points for Broker Layer

| Hook | Change |
|------|--------|
| `RiskEngine.evaluate` | After strategy risk PASS → `evaluate_broker_order` → broker PASS/FAIL |
| `CandleProcessor._execute_pending` | Broker accept before fill; update broker account on fill |
| `live_intents.py` | Shared broker account state across 160 portfolios |
| `position_management.py` | Exits update broker positions (netting) |
| `batch_summary.py` / API | Expose broker account + broker positions vs strategy legs |
| `risk/sizing.py` | Disable `allow_virtual_leverage` for competition |

## Legacy Data

Pre-rebuild competition positions (2026-09-11 run) were created under virtual leverage. They are **LEGACY_SIMULATION** and must not be converted silently. Owner-approved reset required for clean broker-realistic paper.

## Target Flow (After Rebuild)

```
MARKET DATA → STRATEGY → SIGNAL → STRATEGY RISK → BROKER PRE-TRADE → ORDER ACCEPT/REJECT
  → EXECUTION → BROKER FILL → BROKER POSITION (netted) + STRATEGY LEG (attribution)
  → BROKER ACCOUNT → LEDGER
```
