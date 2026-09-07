# QUANTARA — Canonical Trading Flow

## 1. Purpose

מסמך זה מגדיר את **זרימת המסחר הקנונית** — הסדר, הישויות, והאחריות בכל שלב.
כל Mode (Backtest, Paper, Live) **חייב** לעקוב אחרי זרימה זו, עם adapters שונים בלבד.

## 2. The Canonical Pipeline

```
┌─────────────┐
│ Market Data │  Candles (OHLCV) + Instrument metadata
└──────┬──────┘
       ▼
┌─────────────┐
│  Strategy   │  Pure logic → Signal (BUY/SELL/HOLD/CLOSE/MODIFY)
└──────┬──────┘
       ▼
┌─────────────┐
│ Decision Log│  ALWAYS logged — even HOLD, NO_SETUP, DENIED
└──────┬──────┘
       ▼
┌─────────────┐
│ Risk Engine │  Approve → OrderIntent | Deny → DENIED
└──────┬──────┘
       ▼
┌─────────────┐
│Order Intent │  Sized order request (direction, qty, SL, TP)
└──────┬──────┘
       ▼
┌─────────────┐
│  Execution  │  BrokerAdapter / SimulatedBroker
└──────┬──────┘
       ▼
┌─────────────┐
│    Order    │  Submitted order record
└──────┬──────┘
       ▼
┌─────────────┐
│    Fill     │  Execution price, fees, slippage
└──────┬──────┘
       ▼
┌─────────────┐
│  Position   │  Open position tracking
└──────┬──────┘
       ▼
┌─────────────┐
│    Exit     │  SL / TP / Strategy exit / Manual
└──────┬──────┘
       ▼
┌─────────────┐
│    Trade    │  Closed round-trip record
└──────┬──────┘
       ▼
┌─────────────┐
│    P&L      │  Realized + Unrealized
└──────┬──────┘
       ▼
┌─────────────┐
│  Analytics  │  Metrics computation
└──────┬──────┘
       ▼
┌─────────────┐
│  Research   │  Improvement loop (human)
└─────────────┘
```

## 3. Entity Definitions

### 3.1 Candle (Market Data)

| Field | Type | Notes |
|-------|------|-------|
| instrument_id | FK | XAU/USD |
| timeframe | enum | 5m, 15m, 1h |
| timestamp | timestamptz | **Candle open time, UTC** |
| open, high, low, close | decimal | OHLC |
| volume | decimal | nullable |
| source | string | provider id |
| is_complete | bool | false for forming candle in paper |

**Rule:** Strategy receives only candles where `timestamp <= clock.current_candle_time()`.

### 3.2 Signal (Strategy Output)

| Field | Type | Notes |
|-------|------|-------|
| action | enum | BUY, SELL, HOLD, CLOSE, MODIFY |
| instrument_id | FK | |
| strategy_version_id | FK | Immutable reference |
| timestamp | timestamptz | Decision time |
| reason | string | Human-readable |
| confidence | decimal | 0.0–1.0, optional |
| suggested_sl | decimal | optional |
| suggested_tp | decimal | optional |
| metadata | JSONB | indicator values, etc. |

**Rule:** HOLD and NO_SETUP are valid outputs — must be logged.

### 3.3 Decision Log Entry

Every strategy evaluation produces a decision record:

| decision_type | When |
|---------------|------|
| HOLD | Strategy returned HOLD |
| BUY_SIGNAL | Strategy returned BUY |
| SELL_SIGNAL | Strategy returned SELL |
| CLOSE_SIGNAL | Strategy returned CLOSE |
| NO_SETUP | Strategy found no valid setup |
| RISK_DENIED | Risk engine rejected |
| RISK_APPROVED | Risk approved → intent created |
| POSITION_OPEN | Skipped — position already exists |
| TRADING_HALTED | System or risk halt active |
| EXECUTION_FAILED | Broker/adapter error |

### 3.4 OrderIntent (Risk Engine Output)

| Field | Type | Notes |
|-------|------|-------|
| direction | enum | LONG, SHORT |
| quantity | decimal | Position size (units/lots) |
| entry_type | enum | MARKET, LIMIT |
| limit_price | decimal | nullable |
| stop_loss | decimal | required for new positions |
| take_profit | decimal | optional |
| target_risk_amount | decimal | target $ at risk (equity × risk %) |
| actual_risk_amount | decimal | actual $ at risk after quantity caps |
| signal_candle_timestamp | timestamptz | candle that produced the signal |
| execution_candle_timestamp | timestamptz | nullable — next candle open for fill |
| signal_id | FK | traceability |
| risk_profile_id | FK | which profile applied |

### 3.5 Order

| Field | Type | Notes |
|-------|------|-------|
| intent_id | FK | |
| status | enum | PENDING, SUBMITTED, FILLED, PARTIAL, CANCELLED, REJECTED |
| submitted_at | timestamptz | |
| broker_order_id | string | nullable (paper: internal id) |

### 3.6 Fill

| Field | Type | Notes |
|-------|------|-------|
| order_id | FK | |
| fill_price | decimal | **includes spread + slippage** |
| fill_quantity | decimal | |
| fees | decimal | explicit fees — subtracted in net P&L only |
| slippage | decimal | attribution — already in fill_price |
| spread_cost | decimal | attribution — already in fill_price |
| base_price | decimal | nullable — raw price before adjustments |
| filled_at | timestamptz | |

### 3.7 Position

| Field | Type | Notes |
|-------|------|-------|
| instrument_id | FK | |
| direction | enum | LONG, SHORT |
| quantity | decimal | |
| entry_price | decimal | weighted avg |
| stop_loss | decimal | |
| take_profit | decimal | |
| status | enum | OPEN, CLOSED |
| opened_at | timestamptz | |
| closed_at | timestamptz | nullable |
| unrealized_pnl | decimal | updated each tick |
| strategy_version_id | FK | |
| portfolio_id | FK | |

### 3.8 Trade (Closed Position)

| Field | Type | Notes |
|-------|------|-------|
| position_id | FK | |
| entry_price, exit_price | decimal | fill prices (include spread/slippage) |
| quantity | decimal | |
| gross_pnl | decimal | from fill prices |
| realized_pnl | decimal | gross_pnl - fees |
| fees_total | decimal | |
| slippage_total | decimal | attribution — not subtracted again |
| spread_total | decimal | attribution — not subtracted again |
| target_risk_amount | decimal | |
| actual_risk_amount | decimal | |
| duration_seconds | int | |
| exit_reason | enum | SL, TP, STRATEGY, MANUAL, RISK_HALT |
| opened_at, closed_at | timestamptz | |

### 3.9 Portfolio Snapshot

| Field | Type | Notes |
|-------|------|-------|
| portfolio_id | FK | |
| timestamp | timestamptz | |
| balance | decimal | initial + cumulative realized net P&L |
| equity | decimal | balance + unrealized_pnl |
| exposure_notional | decimal | tracking |
| reserved_capital | decimal | Phase 1 = exposure_notional |
| unrealized_pnl | decimal | |
| drawdown | decimal | from peak equity |
| open_positions_count | int | |

## 4. Flow by Trigger

### 4.1 New Candle (Paper / Live) — Default `next_open`

```
When complete candle N arrives:
1. Idempotency check
2. EXECUTE pending intents at candle N open (from signals on N-1)
3. CHECK SL/TP on candle N OHLC
4. UPDATE unrealized P&L (mark at N close)
5. For each active StrategyInstance:
   a. Load candles up to N (no future)
   b. Strategy.evaluate() → Signal
   c. Write DecisionLog entry
   d. If signal.action in (BUY, SELL) AND no conflicting open position:
      i.  RiskEngine.evaluate(signal, portfolio) → OrderIntent | DENIED
      ii. Write DecisionLog (RISK_APPROVED | RISK_DENIED)
      iii. If approved: queue OrderIntent for candle N+1 open (pending_execution)
   e. If signal.action == CLOSE AND open position exists:
      → Queue close for N+1 open (default) or SL/TP path
   f. If signal.action == HOLD: log only
   g. If signal.action == MODIFY: validate SL/TP update
6. Create portfolio snapshot
```

**Signal candle vs execution candle:**
- Signal always references **signal candle N** (the candle strategy evaluated).
- MARKET entry default fill at **candle N+1 open** — no fill at N close.

### 4.2 Backtest (Historical Replay)

Same step order as 4.1 per candle index `i`.
ExecutionAdapter = SimulatedBroker with same fill_timing and P&L rules as Paper.

### 4.3 Position Exit (SL/TP)

```
1. On each candle (or tick in future):
   - For LONG: SL hit if candle.low <= stop_loss
   - For LONG: TP hit if candle.high >= take_profit
   - For SHORT: inverse logic
2. If both SL and TP hit same candle → SL takes priority (conservative)
3. Create exit Fill at trigger price (+ slippage)
4. Close Position → create Trade
5. Update Portfolio cash/equity
6. Log Decision: SL_TRIGGERED | TP_TRIGGERED
```

## 5. Ownership Rules

| Calculation | Owner |
|-------------|-------|
| Indicator values | Strategy |
| Signal action | Strategy |
| Position sizing | Risk Engine |
| SL/TP validation | Risk Engine |
| Fee calculation | Execution Adapter (separate from fill_price) |
| Spread/slippage in fill_price | Execution Adapter |
| Spread/slippage attribution storage | Execution Adapter |
| Unrealized P&L | Portfolio Service |
| Realized P&L (gross) | Portfolio Service — from fill prices |
| Realized P&L (net) | Portfolio Service — gross - fees only |
| Balance update | Portfolio Service — on trade close only |
| Drawdown | Analytics Service |
| Win rate, profit factor | Analytics Service |

**UI never computes P&L or signals.**

## 6. Multi-Strategy / Multi-Instrument

```
Portfolio
  ├── StrategyInstance A (Gold, 1h, Conservative profile)
  ├── StrategyInstance B (Gold, 15m, Aggressive profile)  [future experiment]
  └── Shared instrument exposure tracked at Portfolio level
```

Risk Engine aggregates exposure across instances before approving new intents.

## 7. Idempotency Keys

| Operation | Key |
|-----------|-----|
| Candle processing | `{instrument_id}:{timeframe}:{candle_timestamp}` |
| Signal generation | `{strategy_instance_id}:{candle_timestamp}` |
| Order submission | `{signal_id}:{intent_hash}` |
| Backtest run | `{backtest_run_id}:{candle_timestamp}:{strategy_instance_id}` |

Duplicate key → skip with log, never duplicate trade.

## 8. Mode Parity Rules

| Rule | Description |
|------|-------------|
| Same Strategy class | Identical `evaluate()` in Backtest and Paper |
| Same Risk rules | Same RiskEngine code |
| Same SL/TP logic | Same exit checker |
| Different only | Clock, DataProvider, ExecutionAdapter, fee config |

## 9. What Strategy Must NOT Do

- Query database directly
- Submit orders
- Calculate position size (only suggest SL/TP)
- Know portfolio balance
- Access future candles
- Branch on mode (`if backtest:` forbidden)

## 10. Sequence Diagram (Paper Mode)

```
Worker          MarketData       Strategy        RiskEngine       PaperBroker       Portfolio
  │                 │                │                │                  │                │
  │──fetch candle──►│                │                │                  │                │
  │◄──candle────────│                │                │                  │                │
  │──store──────────────────────────────────────────────────────────────────────────────►│
  │                 │                │                │                  │                │
  │──evaluate(candles, ctx)─────────►│                │                  │                │
  │◄──signal─────────────────────────│                │                  │                │
  │──log decision────────────────────────────────────────────────────────────────────────►│
  │                 │                │                │                  │                │
  │──evaluate(signal, portfolio)─────────────────────►│                  │                │
  │◄──OrderIntent─────────────────────────────────────│                  │                │
  │──log decision────────────────────────────────────────────────────────────────────────►│
  │                 │                │                │                  │                │
  │──execute(intent)──────────────────────────────────────────────────►│                │
  │◄──Order, Fill───────────────────────────────────────────────────────│                │
  │──update position────────────────────────────────────────────────────────────────────►│
  │──check SL/TP────────────────────────────────────────────────────────────────────────►│
  │──snapshot───────────────────────────────────────────────────────────────────────────►│
```

## 11. Cross-References

- Database tables: `04_DATABASE_MODEL.md`
- Strategy contract: `05_STRATEGY_FRAMEWORK.md`
- Risk rules: `06_RISK_ENGINE.md`
- Paper engine: `07_PAPER_TRADING_ENGINE.md`
- Backtest: `08_BACKTESTING_SPEC.md`
