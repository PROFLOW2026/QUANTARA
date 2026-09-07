# QUANTARA — Risk Engine

## 1. Purpose

Risk Engine הוא **מודול נפרד לחלוטין** מה-Strategy.

- Strategy **מציעה** (BUY/SELL + suggested SL/TP)
- Risk Engine **מאשר, מתאם, או דוחה** (OrderIntent | DENIED)

## 2. Design Principles

1. **Separation** — Strategy never sizes positions
2. **Portfolio-aware** — considers total exposure, not just single trade
3. **Profile-driven** — Conservative/Balanced/Aggressive configs
4. **Deterministic** — same inputs → same decision
5. **Always logged** — every evaluation → Decision Log
6. **Halt capability** — can pause all trading

## 3. Input / Output

### 3.1 Input

```python
@dataclass
class RiskEvaluationInput:
    signal: Signal
    strategy_instance: StrategyInstance
    portfolio: Portfolio
    open_positions: list[Position]
    risk_profile: RiskProfile
    current_candle: Candle
    all_active_instances: list[StrategyInstance]  # for cross-strategy exposure
```

### 3.2 Output

```python
@dataclass
class RiskDecision:
    approved: bool
    intent: Optional[OrderIntent] = None
    denial_reason: Optional[str] = None
    checks_passed: list[str] = None
    checks_failed: list[str] = None
    metadata: Optional[dict] = None
```

## 4. Evaluation Pipeline

```
Signal received
  │
  ├─► 1. Trading Halt Check ──────────► DENIED if halted
  │
  ├─► 2. Signal Action Filter ────────► Skip if HOLD/CLOSE (different path)
  │
  ├─► 3. Open Position Check ─────────► DENIED if same-direction position exists
  │                                      (unless strategy allows pyramiding — default: NO)
  │
  ├─► 4. Max Open Positions ──────────► DENIED if count >= limit
  │
  ├─► 5. Instrument Exposure ─────────► DENIED if total exposure > max
  │
  ├─► 6. Daily Loss Limit ────────────► DENIED if daily loss exceeded
  │
  ├─► 7. Max Drawdown ────────────────► DENIED + HALT if breached
  │
  ├─► 8. Volatility Check ────────────► DENIED if ATR too high (optional)
  │
  ├─► 9. Stop Loss Validation ────────► DENIED if SL missing or too tight/wide
  │
  ├─► 10. Position Sizing ────────────► Calculate desired_quantity → actual_quantity
  │
  ├─► 11. Target vs Actual Risk ──────► Store target_risk_amount + actual_risk_amount
  │
  └─► 12. APPROVED → OrderIntent (pending_execution at next open)
```

## 5. Risk Rules Detail

### 5.1 Risk Per Trade (Position Sizing)

**Risk % is a target/maximum — not a guarantee the full amount is always used.**

After exposure/capital caps, `actual_risk_amount` may be less than `target_risk_amount`.
Both values are stored on OrderIntent and displayed in UI.

**Formula (fixed fractional):**

```
target_risk_amount = portfolio.equity × (risk_profile.risk_per_trade_pct / 100)

entry_reference = estimated next-open price for sizing
  (default: signal candle close as proxy — actual fill at next open)

sl_distance = abs(entry_reference - stop_loss)

desired_quantity = target_risk_amount / sl_distance

actual_quantity = desired_quantity
  capped by:
    - max_total_exposure_pct (remaining exposure headroom)
    - available_capital (balance - reserved_capital)
    - instrument min_quantity
  floored/rounded to quantity_step (NOT price_tick_size)

actual_quantity = max(actual_quantity, 0)

actual_risk_amount = actual_quantity × sl_distance

If actual_quantity < min_quantity → DENY: QUANTITY_BELOW_MINIMUM
```

**Units:** quantity in **instrument units** (troy oz for XAU/USD).
Rounding uses `quantity_step` and `min_quantity` from instrument metadata.

| Profile | risk_per_trade_pct | Example target ($10k equity) |
|---------|-------------------|------------------------------|
| Conservative | 0.5% | $50 target risk |
| Balanced | 1.0% | $100 target risk |
| Aggressive | 2.0% | $200 target risk |

**No arbitrary max_position_size caps in seed profiles** — exposure limits are the primary size constraints.

### 5.2 Maximum Open Positions

| Profile | max_open_positions |
|---------|-------------------|
| Conservative | 1 |
| Balanced | 2 |
| Aggressive | 3 |

Counts positions across **all strategy instances** in same portfolio.

### 5.3 Maximum Total Exposure

```
total_exposure = sum(position.quantity × current_price for all open positions)
exposure_pct = total_exposure / portfolio.equity × 100

DENY if exposure_pct > risk_profile.max_total_exposure_pct
```

| Profile | max_total_exposure_pct |
|---------|----------------------|
| Conservative | 50% |
| Balanced | 100% |
| Aggressive | 100% |

### 5.4 Daily Loss Limit

```
daily_pnl = sum(realized_pnl today) + sum(unrealized_pnl of positions opened today)
daily_loss_pct = abs(min(daily_pnl, 0)) / day_start_equity × 100

DENY new trades if daily_loss_pct >= risk_profile.daily_loss_limit_pct
HALT if daily_loss_pct >= daily_loss_limit_pct × 1.5
```

| Profile | daily_loss_limit_pct |
|---------|---------------------|
| Conservative | 1.5% |
| Balanced | 3.0% |
| Aggressive | 5.0% |

### 5.5 Maximum Drawdown

```
drawdown_pct = (peak_equity - current_equity) / peak_equity × 100

DENY if drawdown_pct >= max_drawdown_pct
HALT portfolio if drawdown_pct >= max_drawdown_pct (status → halted)
```

| Profile | max_drawdown_pct |
|---------|-----------------|
| Conservative | 5% |
| Balanced | 10% |
| Aggressive | 15% |

### 5.6 Volatility Limits (Optional)

```
If ATR(14) / price > volatility_threshold:
  DENY — "VOLATILITY_TOO_HIGH"

Configurable via risk_profile.parameters
Default: disabled in v1, enabled flag available
```

### 5.7 Stop Loss Validation

| Check | Rule |
|-------|------|
| Required | BUY/SELL must have suggested_sl |
| Direction | LONG: SL < entry; SHORT: SL > entry |
| Min distance | SL distance >= 0.5 × ATR(14) |
| Max distance | SL distance <= 5.0 × ATR(14) |
| Override | Risk can adjust SL wider (never tighter without strategy MODIFY) |

### 5.8 Cross-Strategy Exposure (Same Instrument)

When multiple strategy instances trade XAU/USD:

```
combined_exposure = sum of all XAU/USD position values in portfolio
if combined_exposure > instrument_exposure_cap:
  DENY — "INSTRUMENT_EXPOSURE_LIMIT"
```

Default: instrument cap = max_total_exposure_pct (same as portfolio level).

## 6. Risk Profiles (Seed Defaults)

```json
[
  {
    "slug": "conservative",
    "name": "Conservative",
    "risk_per_trade_pct": 0.5,
    "max_open_positions": 1,
    "max_total_exposure_pct": 50,
    "daily_loss_limit_pct": 1.5,
    "max_drawdown_pct": 5
  },
  {
    "slug": "balanced",
    "name": "Balanced",
    "risk_per_trade_pct": 1.0,
    "max_open_positions": 2,
    "max_total_exposure_pct": 100,
    "daily_loss_limit_pct": 3.0,
    "max_drawdown_pct": 10
  },
  {
    "slug": "aggressive",
    "name": "Aggressive",
    "risk_per_trade_pct": 2.0,
    "max_open_positions": 3,
    "max_total_exposure_pct": 100,
    "daily_loss_limit_pct": 5.0,
    "max_drawdown_pct": 15
  }
]
```

Profiles differ by **risk %, exposure, positions, halt thresholds** — not by arbitrary quantity caps that would mute Aggressive vs Conservative.

All values **configurable** via DB — seeds are defaults only.

## 7. Halt / Pause States

| State | Trigger | Effect |
|-------|---------|--------|
| ACTIVE | Normal | Trading allowed |
| HALTED_DRAWDOWN | Max drawdown breached | No new trades, existing SL/TP still active |
| HALTED_DAILY_LOSS | Daily loss exceeded 1.5× | No new trades until next day |
| HALTED_MANUAL | User pause in Settings | No new trades |
| HALTED_SYSTEM | Worker error / data stale | No new trades until recovery |

Resume: manual only (Settings or API) except daily loss (auto at midnight UTC).

## 8. CLOSE Signal Handling

Risk Engine does **not** size CLOSE signals.

```
If signal.action == CLOSE and open position exists:
  → OrderIntent with quantity = position.quantity, direction = opposite
  → Skip sizing rules
  → Still log decision
```

## 9. Backtest Parity

**Identical RiskEngine code** runs in Backtest and Paper.

Execution assumptions (spread/slippage) differ — not risk rules.

Backtest records which risk_profile was used in `backtest_runs.parameters`.

## 10. Decision Log Messages

| Result | Message Example |
|--------|----------------|
| Approved | `RISK_APPROVED: qty=0.1, target_risk=$100, actual_risk=$40, SL=2645.50` |
| Denied — quantity | `RISK_DENIED: QUANTITY_BELOW_MINIMUM after caps` |
| Denied — position | `RISK_DENIED: POSITION_ALREADY_OPEN` |
| Denied — exposure | `RISK_DENIED: MAX_EXPOSURE (105% > 100%)` |
| Denied — daily loss | `RISK_DENIED: DAILY_LOSS_LIMIT (3.2% >= 3.0%)` |
| Denied — drawdown | `RISK_DENIED: MAX_DRAWDOWN + HALT TRIGGERED` |
| Denied — SL | `RISK_DENIED: INVALID_STOP_LOSS (missing)` |
| Denied — volatility | `RISK_DENIED: VOLATILITY_TOO_HIGH` |

## 11. Configuration vs Code

| In Code (fixed) | In Config (risk_profile) |
|-----------------|---------------------------|
| Evaluation pipeline order | All percentage thresholds |
| Sizing formula type | risk_per_trade_pct |
| SL validation rules structure | min/max ATR multiples |
| Halt behavior logic | drawdown/daily limits |

## 12. Cross-References

- Flow: `03_CANONICAL_TRADING_FLOW.md`
- DB: `04_DATABASE_MODEL.md` (risk_profiles)
- Paper: `07_PAPER_TRADING_ENGINE.md`
- Backtest: `08_BACKTESTING_SPEC.md`
