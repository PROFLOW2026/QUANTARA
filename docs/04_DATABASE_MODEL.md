# QUANTARA — Database Model

## 1. Principles

1. **Immutable history** — trades, signals, decisions, fills are append-only
2. **Version everything strategic** — strategy_versions, never mutate in place
3. **UTC timestamps** — all `timestamptz` stored in UTC
4. **Traceability** — every trade links to signal → strategy_version → parameters
5. **Mode agnostic core** — same tables for Backtest and Paper (via `run_context`)
6. **Single user ready** — `owner_id` on root entities, default single owner

## 2. Entity Relationship Overview

```
instruments ──┬── candles
              │
strategies ───┼── strategy_versions
              │
risk_profiles │
              │
portfolios ───┼── strategy_instances ──┬── signals
              │                        ├── decisions
              │                        ├── order_intents
              │                        ├── orders ── fills
              │                        ├── positions ── trades
              │                        └── portfolio_snapshots
              │
backtest_runs ─┘ (links to portfolio + strategy_instance)

market_data_providers
worker_jobs
worker_runs
events
settings
experiments (future structure)
```

## 3. Core Tables

### 3.1 instruments

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| symbol | VARCHAR(20) | UNIQUE, e.g. `XAUUSD` |
| name | VARCHAR(100) | e.g. `Gold / US Dollar` |
| asset_class | ENUM | commodity, forex, index, crypto, stock |
| base_currency | VARCHAR(10) | XAU |
| quote_currency | VARCHAR(10) | USD |
| pip_size | DECIMAL | e.g. 0.01 |
| contract_size | DECIMAL | units per 1 lot (e.g. 100 oz) |
| price_tick_size | DECIMAL | minimum price increment (e.g. 0.01) |
| quantity_step | DECIMAL | minimum quantity increment (e.g. 0.01 oz) |
| min_quantity | DECIMAL | minimum order quantity |
| trading_sessions | JSONB | session definitions |
| is_active | BOOLEAN | default true |
| metadata | JSONB | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 3.2 candles

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| instrument_id | UUID | FK → instruments |
| timeframe | ENUM | 5m, 15m, 1h |
| timestamp | TIMESTAMPTZ | candle open, UTC |
| open, high, low, close | DECIMAL(18,8) | |
| volume | DECIMAL(18,4) | nullable |
| source | VARCHAR(50) | provider name |
| is_complete | BOOLEAN | default true |
| created_at | TIMESTAMPTZ | |

**Unique:** `(instrument_id, timeframe, timestamp, source)`

**Index:** `(instrument_id, timeframe, timestamp DESC)`

### 3.3 strategies

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| slug | VARCHAR(50) | UNIQUE, e.g. `gold-trend-pullback` |
| name | VARCHAR(100) | |
| description | TEXT | |
| supported_instruments | UUID[] | array of instrument IDs |
| supported_timeframes | ENUM[] | |
| status | ENUM | draft, active, deprecated, archived |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 3.4 strategy_versions

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| strategy_id | UUID | FK → strategies |
| version | VARCHAR(20) | e.g. `1.0.0` |
| version_major | INT | |
| version_minor | INT | |
| version_patch | INT | |
| parameters | JSONB | configurable params + defaults |
| parameters_schema | JSONB | JSON Schema for validation |
| risk_profile_compatibility | ENUM[] | conservative, balanced, aggressive |
| logic_hash | VARCHAR(64) | hash of strategy code |
| changelog | TEXT | |
| is_active | BOOLEAN | |
| created_at | TIMESTAMPTZ | |

**Unique:** `(strategy_id, version)`

**Rule:** UPDATE on parameters after creation = forbidden. New row only.

### 3.5 risk_profiles

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| name | VARCHAR(50) | Conservative, Balanced, Aggressive |
| slug | ENUM | conservative, balanced, aggressive |
| risk_per_trade_pct | DECIMAL | e.g. 0.5, 1.0, 2.0 |
| max_open_positions | INT | |
| max_total_exposure_pct | DECIMAL | |
| daily_loss_limit_pct | DECIMAL | |
| max_drawdown_pct | DECIMAL | |
| volatility_limit_atr_mult | DECIMAL | nullable |
| is_default | BOOLEAN | |
| parameters | JSONB | additional rules |
| created_at | TIMESTAMPTZ | |

### 3.6 portfolios

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| owner_id | UUID | single user |
| name | VARCHAR(100) | e.g. `Paper Main`, `Backtest Run #42` |
| mode | ENUM | backtest, paper, live_manual, live_automated |
| initial_capital | DECIMAL(18,2) | |
| balance | DECIMAL(18,2) | initial + cumulative realized net P&L |
| unrealized_pnl | DECIMAL(18,2) | mark-to-market of open positions |
| equity | DECIMAL(18,2) | balance + unrealized_pnl |
| exposure_notional | DECIMAL(18,2) | sum(open qty × mark price) — tracking only |
| reserved_capital | DECIMAL(18,2) | Phase 1: equals exposure_notional for limit checks |
| currency | VARCHAR(10) | USD |
| status | ENUM | active, halted, closed |
| halt_reason | TEXT | nullable |
| peak_equity | DECIMAL(18,2) | for drawdown |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 3.7 strategy_instances

Links a strategy version to a portfolio with specific config.

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| portfolio_id | UUID | FK |
| strategy_version_id | UUID | FK |
| instrument_id | UUID | FK |
| timeframe | ENUM | |
| risk_profile_id | UUID | FK |
| parameter_overrides | JSONB | nullable |
| is_active | BOOLEAN | |
| experiment_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

**Unique (active):** `(portfolio_id, strategy_version_id, instrument_id, timeframe)` where is_active

### 3.8 signals

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| strategy_instance_id | UUID | FK |
| strategy_version_id | UUID | FK (denormalized for query) |
| instrument_id | UUID | FK |
| candle_timestamp | TIMESTAMPTZ | trigger candle |
| action | ENUM | buy, sell, hold, close, modify |
| reason | TEXT | |
| confidence | DECIMAL(5,4) | nullable |
| suggested_sl | DECIMAL(18,8) | nullable |
| suggested_tp | DECIMAL(18,8) | nullable |
| metadata | JSONB | indicator snapshot |
| mode | ENUM | backtest, paper, live_* |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

**Index:** `(strategy_instance_id, candle_timestamp)`

### 3.9 decisions

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| strategy_instance_id | UUID | FK |
| signal_id | UUID | FK nullable |
| instrument_id | UUID | FK |
| candle_timestamp | TIMESTAMPTZ | |
| decision_type | ENUM | see 03_CANONICAL_TRADING_FLOW |
| message | TEXT | human-readable |
| metadata | JSONB | |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

**Index:** `(strategy_instance_id, created_at DESC)`

### 3.10 order_intents

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| signal_id | UUID | FK |
| strategy_instance_id | UUID | FK |
| portfolio_id | UUID | FK |
| direction | ENUM | long, short |
| quantity | DECIMAL(18,8) | |
| entry_type | ENUM | market, limit |
| limit_price | DECIMAL(18,8) | nullable |
| stop_loss | DECIMAL(18,8) | |
| take_profit | DECIMAL(18,8) | nullable |
| target_risk_amount | DECIMAL(18,2) | target $ at risk (equity × risk %) |
| actual_risk_amount | DECIMAL(18,2) | actual $ at risk after caps (qty × SL distance) |
| signal_candle_timestamp | TIMESTAMPTZ | candle that generated the signal |
| execution_candle_timestamp | TIMESTAMPTZ | nullable — candle open where fill occurs |
| risk_profile_id | UUID | FK |
| status | ENUM | pending_execution, executed, rejected, expired |
| rejection_reason | TEXT | nullable |
| idempotency_key | VARCHAR(100) | UNIQUE |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

### 3.11 orders

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| intent_id | UUID | FK → order_intents |
| portfolio_id | UUID | FK |
| instrument_id | UUID | FK |
| direction | ENUM | |
| quantity | DECIMAL(18,8) | |
| order_type | ENUM | market, limit |
| status | ENUM | pending, submitted, filled, partial, cancelled, rejected |
| broker_order_id | VARCHAR(100) | nullable |
| submitted_at | TIMESTAMPTZ | |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

### 3.12 fills

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| order_id | UUID | FK |
| position_id | UUID | FK nullable |
| fill_price | DECIMAL(18,8) | **includes spread + slippage** — canonical execution price |
| fill_quantity | DECIMAL(18,8) | |
| fees | DECIMAL(18,4) | explicit fees only — subtracted from net P&L |
| slippage | DECIMAL(18,8) | attribution only — already in fill_price |
| spread_cost | DECIMAL(18,4) | attribution only — already in fill_price |
| base_price | DECIMAL(18,8) | nullable — raw price before spread/slippage (audit) |
| side | ENUM | entry, exit |
| filled_at | TIMESTAMPTZ | |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

### 3.13 positions

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| portfolio_id | UUID | FK |
| strategy_instance_id | UUID | FK |
| instrument_id | UUID | FK |
| direction | ENUM | long, short |
| quantity | DECIMAL(18,8) | |
| entry_price | DECIMAL(18,8) | |
| current_price | DECIMAL(18,8) | |
| stop_loss | DECIMAL(18,8) | |
| take_profit | DECIMAL(18,8) | nullable |
| unrealized_pnl | DECIMAL(18,2) | |
| status | ENUM | open, closed |
| opened_at | TIMESTAMPTZ | |
| closed_at | TIMESTAMPTZ | nullable |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### 3.14 trades

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| position_id | UUID | FK |
| portfolio_id | UUID | FK |
| strategy_instance_id | UUID | FK |
| strategy_version_id | UUID | FK |
| instrument_id | UUID | FK |
| direction | ENUM | |
| quantity | DECIMAL(18,8) | |
| entry_price | DECIMAL(18,8) | |
| exit_price | DECIMAL(18,8) | |
| gross_pnl | DECIMAL(18,2) | (exit_fill - entry_fill) × qty — fills include spread/slippage |
| realized_pnl | DECIMAL(18,2) | gross_pnl - entry_fees - exit_fees |
| fees_total | DECIMAL(18,4) | |
| slippage_total | DECIMAL(18,8) | attribution sum — **not** subtracted again from net |
| spread_total | DECIMAL(18,4) | attribution sum — **not** subtracted again from net |
| target_risk_amount | DECIMAL(18,2) | from order_intent |
| actual_risk_amount | DECIMAL(18,2) | from order_intent |
| exit_reason | ENUM | sl, tp, strategy, manual, risk_halt, end_of_backtest |
| duration_seconds | INT | |
| opened_at | TIMESTAMPTZ | |
| closed_at | TIMESTAMPTZ | |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

**Rule:** No UPDATE or DELETE on trades table.

### 3.15 portfolio_snapshots

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| portfolio_id | UUID | FK |
| timestamp | TIMESTAMPTZ | |
| balance | DECIMAL(18,2) | |
| equity | DECIMAL(18,2) | balance + unrealized_pnl |
| exposure_notional | DECIMAL(18,2) | |
| reserved_capital | DECIMAL(18,2) | |
| unrealized_pnl | DECIMAL(18,2) | |
| drawdown_pct | DECIMAL(8,4) | |
| open_positions_count | INT | |
| metadata | JSONB | |
| mode | ENUM | |
| backtest_run_id | UUID | FK nullable |
| created_at | TIMESTAMPTZ | |

### 3.16 backtest_runs

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| portfolio_id | UUID | FK (virtual portfolio for this run) |
| strategy_instance_id | UUID | FK |
| strategy_version_id | UUID | FK |
| instrument_id | UUID | FK |
| timeframe | ENUM | |
| start_date | TIMESTAMPTZ | |
| end_date | TIMESTAMPTZ | |
| initial_capital | DECIMAL(18,2) | |
| final_capital | DECIMAL(18,2) | nullable until complete |
| status | ENUM | pending, running, completed, failed |
| parameters | JSONB | snapshot of params used |
| execution_assumptions | JSONB | spread, slippage, fees |
| dataset_fingerprint | VARCHAR(64) | SHA256 of canonical OHLCV content — see 08_BACKTESTING_SPEC |
| metrics | JSONB | computed on completion |
| started_at | TIMESTAMPTZ | |
| completed_at | TIMESTAMPTZ | nullable |
| error_message | TEXT | nullable |
| created_at | TIMESTAMPTZ | |

### 3.17 experiments

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| name | VARCHAR(100) | |
| description | TEXT | |
| instrument_id | UUID | FK |
| status | ENUM | draft, running, completed |
| start_date | TIMESTAMPTZ | |
| end_date | TIMESTAMPTZ | nullable |
| created_at | TIMESTAMPTZ | |

Links multiple strategy_instances with separate virtual portfolios.

### 3.18 market_data_providers

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| name | VARCHAR(50) | |
| adapter_class | VARCHAR(100) | Python class path |
| is_active | BOOLEAN | |
| config | JSONB | encrypted refs in env |
| priority | INT | failover order |
| last_success_at | TIMESTAMPTZ | |
| last_error | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3.19 worker_jobs

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| job_type | ENUM | fetch_data, run_strategy, check_sl_tp, snapshot, backtest |
| idempotency_key | VARCHAR(200) | UNIQUE |
| payload | JSONB | |
| status | ENUM | pending, running, completed, failed |
| attempts | INT | |
| max_attempts | INT | default 3 |
| scheduled_at | TIMESTAMPTZ | |
| started_at | TIMESTAMPTZ | |
| completed_at | TIMESTAMPTZ | |
| error_message | TEXT | |
| created_at | TIMESTAMPTZ | |

### 3.20 worker_runs

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| worker_name | VARCHAR(50) | |
| started_at | TIMESTAMPTZ | |
| completed_at | TIMESTAMPTZ | |
| status | ENUM | success, partial, failed |
| jobs_processed | INT | |
| errors | JSONB | |
| created_at | TIMESTAMPTZ | |

### 3.21 events

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| event_type | VARCHAR(50) | |
| entity_type | VARCHAR(50) | |
| entity_id | UUID | |
| payload | JSONB | |
| severity | ENUM | info, warning, error, critical |
| created_at | TIMESTAMPTZ | |

### 3.22 settings

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK |
| key | VARCHAR(100) | UNIQUE |
| value | JSONB | |
| description | TEXT | |
| updated_at | TIMESTAMPTZ | |

Key settings: `display_timezone`, `default_risk_profile`, `paper_trading_enabled`, `default_initial_capital`

## 4. Enums Summary

```sql
-- Conceptual
timeframe: 5m, 15m, 1h
mode: backtest, paper, live_manual, live_automated
signal_action: buy, sell, hold, close, modify
direction: long, short
decision_type: hold, buy_signal, sell_signal, close_signal, no_setup,
               risk_denied, risk_approved, position_open, trading_halted,
               execution_failed, sl_triggered, tp_triggered, system_error
exit_reason: sl, tp, strategy, manual, risk_halt, end_of_backtest
order_status: pending, submitted, filled, partial, cancelled, rejected
portfolio_status: active, halted, closed
strategy_status: draft, active, deprecated, archived
backtest_status: pending, running, completed, failed
```

## 5. Indexing Strategy

| Query Pattern | Index |
|---------------|-------|
| Latest candles | `(instrument_id, timeframe, timestamp DESC)` |
| Decisions today | `(created_at DESC)` + `(strategy_instance_id, created_at DESC)` |
| Open positions | `(portfolio_id, status)` where status=open |
| Trades by strategy | `(strategy_version_id, closed_at DESC)` |
| Backtest history | `(strategy_instance_id, created_at DESC)` |

## 6. Data Integrity Rules

| Rule | Enforcement |
|------|-------------|
| No duplicate candles | UNIQUE constraint |
| No duplicate trades from retry | idempotency_key UNIQUE |
| Trades immutable | No UPDATE/DELETE triggers |
| Strategy version immutable | No UPDATE on parameters |
| FK cascade | positions → portfolio: RESTRICT; fills → orders: RESTRICT |

## 7. Backtest vs Paper Data Isolation

Both use same tables. Differentiation via:

- `portfolios.mode`
- `backtest_run_id` on child records (nullable for paper)
- Paper portfolio: persistent, one main
- Backtest portfolio: created per run, can be archived

## 8. Seed Data (Phase 1)

| Entity | Seed |
|--------|------|
| instruments | XAUUSD |
| risk_profiles | Conservative, Balanced, Aggressive (defaults) |
| strategies | gold-trend-pullback v1.0.0 |
| settings | defaults |

## 9. Cross-References

- Flow: `03_CANONICAL_TRADING_FLOW.md`
- Strategy: `05_STRATEGY_FRAMEWORK.md`
- Analytics queries: `12_ANALYTICS_METRICS.md`
