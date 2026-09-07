# QUANTARA — Paper Trading Engine

## 1. Purpose

Paper Trading Engine מדמה מסחר **באמינות מаксимלית** עם:

- נתוני שוק **אמיתיים**
- כסף **וירטואלי**
- אותה Strategy + Risk logic כמו Backtest

## 2. PaperBrokerAdapter

Implementation של `BrokerAdapter` / `ExecutionAdapter` ל-Paper mode.

```python
class PaperBrokerAdapter(BrokerAdapter):
    """Simulates order execution against real market prices."""
```

### 2.1 Responsibilities

| Responsibility | Owner |
|----------------|-------|
| Accept OrderIntent | PaperBrokerAdapter |
| Simulate fill price | PaperBrokerAdapter |
| Apply spread + slippage **into fill_price** | PaperBrokerAdapter |
| Calculate fees (separate from fill_price) | PaperBrokerAdapter |
| Create Order + Fill records | PaperBrokerAdapter |
| Open/update/close Position | Portfolio Service |
| Update balance/equity | Portfolio Service |

## 3. Portfolio Model (Paper) — Phase 1

### 3.1 Canonical Accounting

```
balance = initial_capital + cumulative_realized_net_pnl

unrealized_pnl = sum(mark-to-market of open positions)

equity = balance + unrealized_pnl

exposure_notional = sum(quantity × mark_price) for open positions
reserved_capital = exposure_notional   # Phase 1 simple tracking — not deducted from balance
available_capital = balance - reserved_capital   # for exposure cap checks
```

**אין** הפחתת full notional מ-balance בכניסה לפוזיציה.
שווי הפוזיציה נכנס ל-equity דרך `unrealized_pnl`, לא נעלם.

### 3.2 LONG Position

**On entry (fill recorded):**
```
entry_fill_price = execution price (includes spread + slippage)
exposure_notional += quantity × mark_price
unrealized_pnl += (mark_price - entry_fill_price) × quantity
# balance unchanged on entry
```

**Mark-to-market (each candle):**
```
unrealized_pnl = (current_price - entry_fill_price) × quantity
equity = balance + unrealized_pnl
```

**On exit (fill recorded):**
```
exit_fill_price = execution price (includes spread + slippage)
gross_pnl = (exit_fill_price - entry_fill_price) × quantity
net_pnl = gross_pnl - entry_fees - exit_fees
balance += net_pnl
exposure_notional -= quantity × mark_price (position closed)
```

### 3.3 SHORT Position

**On entry:**
```
entry_fill_price = execution price (includes spread + slippage)
exposure_notional += quantity × mark_price
unrealized_pnl += (entry_fill_price - mark_price) × quantity
# balance unchanged on entry
```

**Mark-to-market:**
```
unrealized_pnl = (entry_fill_price - current_price) × quantity
equity = balance + unrealized_pnl
```

**On exit:**
```
gross_pnl = (entry_fill_price - exit_fill_price) × quantity
net_pnl = gross_pnl - entry_fees - exit_fees
balance += net_pnl
```

SHORT משתמש **באותה מסגרת חשבונאית** — לא "inverse logic" נפרדת.

### 3.4 Initial Capital

Default: **$10,000 USD** — configurable in Settings.

At creation: `balance = initial_capital`, `equity = initial_capital`.

One persistent **Paper Portfolio** per owner (`portfolios.mode = paper`).

## 4. Execution Simulation

### 4.1 Canonical Fill Price Model

**`fill_price` כולל spread + slippage.** Fees נשמרים בנפרד.

```
base_price = raw market price (open/SL/TP level — see timing below)
half_spread = spread / 2
slippage_amount = slippage_pct × base_price

LONG entry:  fill_price = base_price + half_spread + slippage_amount
LONG exit:   fill_price = base_price - half_spread - slippage_amount
SHORT entry: fill_price = base_price - half_spread - slippage_amount
SHORT exit:  fill_price = base_price + half_spread + slippage_amount

fees = quantity × fill_price × fee_rate   # separate — subtracted only in net_pnl
```

Store on Fill: `fill_price`, `base_price`, `spread_cost`, `slippage`, `fees`.

**Attribution fields (`spread_cost`, `slippage`) are for analytics — never subtracted again if already in fill_price.**

### 4.2 Default Execution Timing — `next_open`

**Canonical default:** `fill_timing = next_open`

```
Candle N closes
  → Strategy evaluates Candle N
  → Signal generated
  → Risk evaluates
  → OrderIntent queued (status: pending_execution)

Candle N+1 opens
  → MARKET entry executed at N+1 open (+ spread/slippage)
```

Strategy **never** assumes fill at Candle N close when deciding.

`candle_close` execution — **research option only**, not default. Document if used.

### 4.3 Entry Fill (MARKET, default next_open)

```
For LONG entry approved on signal candle N:
  base_price = candle[N+1].open
  fill_price = base_price + spread/2 + slippage
  filled_at = candle[N+1].timestamp
```

| Parameter | Default (XAUUSD) | Config Location |
|-----------|-----------------|-----------------|
| spread | $0.30 | settings / instrument metadata |
| slippage_pct | 0.01% | execution_assumptions |
| fee_rate | 0% | execution_assumptions |
| fill_timing | **next_open** | execution_assumptions |

### 4.4 Exit Fill

Same spread/slippage model applied to exit base price.

**Strategy CLOSE / manual exit:** default `next_open` after signal candle (same as entry).

**SL/TP trigger:** evaluated on candle OHLC during the trigger candle; fill at trigger level ± slippage (clamped to candle range). Not deferred to next open.

### 4.5 SL/TP Trigger

Checked on **each complete candle** against OHLC:

| Direction | Stop Loss | Take Profit |
|-----------|-----------|-------------|
| LONG | candle.low <= stop_loss | candle.high >= take_profit |
| SHORT | candle.high >= stop_loss | candle.low <= take_profit |

**Same-candle conflict:** SL wins (conservative).

Fill: trigger price as base, then apply spread/slippage (not beyond candle range).

## 5. P&L Calculations (Canonical)

### 5.1 Unrealized P&L

Uses **fill_price** (includes spread/slippage) as entry reference:

```
LONG:  unrealized = (current_price - entry_fill_price) × quantity
SHORT: unrealized = (entry_fill_price - current_price) × quantity

current_price = latest candle close
```

### 5.2 Realized P&L (Closed Trade)

```
LONG:  gross_pnl = (exit_fill_price - entry_fill_price) × quantity
SHORT: gross_pnl = (entry_fill_price - exit_fill_price) × quantity

net_pnl (realized_pnl) = gross_pnl - entry_fees - exit_fees
```

**Do NOT subtract spread_total or slippage_total again** — already embedded in fill prices.

`trades.spread_total` and `trades.slippage_total` = sum of attribution fields for analytics only.

### 5.3 Drawdown

```
peak_equity = max(peak_equity, equity)
drawdown = (peak_equity - equity) / peak_equity × 100
```

## 6. Entity Lifecycle (Paper)

```
Candle N: Signal → Risk → OrderIntent (pending_execution, exec at N+1)
  │
Candle N+1 open: Execute → Order → Fill (entry)
  │
  ▼
Position (open)
  │
  ├──► [each candle] update unrealized_pnl, exposure_notional
  ├──► [SL/TP hit] Exit Fill → Trade
  ├──► [Strategy CLOSE] queue → next open → Exit Fill → Trade
  │
  ▼
Trade (immutable)
  │
  ▼
balance += net_pnl; equity recalculated
```

## 7. Paper vs Backtest

| Aspect | Paper | Backtest |
|--------|-------|----------|
| Clock | Real time | Simulated candle time |
| Data | Live candles | Historical replay |
| Portfolio | Persistent | Per-run virtual |
| fill_timing default | **next_open** | **next_open** |
| Strategy code | **Identical** | **Identical** |
| Risk code | **Identical** | **Identical** |
| Fill/P&L model | **Identical** | **Identical** |

## 8. Paper Trading Worker Cycle

```
When new complete candle N arrives:
  1. Execute pending intents queued for this candle's OPEN (from signal on N-1)
  2. Check SL/TP on candle N OHLC
  3. Update unrealized P&L (mark at N close)
  4. Strategy.evaluate on candle N → Signal → Risk
  5. If approved entry/CLOSE: queue intent for candle N+1 open
  6. Create portfolio snapshot
  7. Check halt conditions
```

## 9. Idempotency

| Operation | Key |
|-----------|-----|
| Process candle | `{instrument}:{timeframe}:{timestamp}` |
| Execute intent | `{intent_id}:exec:{execution_candle_timestamp}` |
| SL/TP check | `{position_id}:{candle_timestamp}` |

## 10. Configuration (Settings)

```json
{
  "paper_trading_enabled": true,
  "paper_initial_capital": 10000,
  "paper_default_spread": 0.30,
  "paper_default_slippage_pct": 0.0001,
  "paper_default_fee_rate": 0,
  "paper_fill_timing": "next_open"
}
```

## 11. Error Handling

| Error | Behavior |
|-------|----------|
| Stale data | HALT new trades, log event |
| Provider failure | Retry 3×, then halt |
| Exposure/capital limit | DENY — `INSUFFICIENT_AVAILABLE_CAPITAL` |
| Fill simulation error | Log, skip, no partial state |

See `14_ERROR_AND_RECOVERY_RULES.md`.

## 12. Cross-References

- Flow: `03_CANONICAL_TRADING_FLOW.md`
- Risk: `06_RISK_ENGINE.md`
- Backtest parity: `08_BACKTESTING_SPEC.md`
- Workers: `13_BACKGROUND_WORKERS.md`
- DB: `04_DATABASE_MODEL.md`
