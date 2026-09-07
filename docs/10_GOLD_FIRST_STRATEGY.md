# QUANTARA — Gold First Strategy (v1.0.0)

## 1. Purpose & Philosophy

**Gold Trend Pullback Strategy v1.0.0** היא Strategy **ראשונה** שמטרתה:

1. ✅ להוכיח את כל השרשרת (Data → Signal → Risk → Execution → P&L → Analytics)
2. ✅ להיות **שקופה, מדידה, וניתנת לבדיקה**
3. ✅ ללמד trend-following + pullback mechanics על XAU/USD
4. ❌ **לא** להבטיח רווח
5. ❌ **לא** להיות "רובוט קסם"

> **Disclaimer:** מדובר בנקודת התחלה למחקר. תוצאות עבר אינן ערובה לעתיד.

## 2. Why This Strategy?

| Criterion | How This Strategy Fits |
|-----------|-------------------------|
| Infrastructure proof | Uses trend, momentum, ATR — exercises full pipeline |
| Transparency | Every rule is explicit IF/THEN |
| Measurability | Clear entry/exit with numeric thresholds |
| Classic approach | EMA trend + pullback — well understood, debuggable |
| Risk integration | ATR-based SL/TP — tests Risk Engine sizing |
| Not arbitrary | Based on established trend-pullback concept |
| Learning value | Teaches trend filtering, pullback entries, ATR stops |

**Why not something simpler (e.g., MA crossover only)?**
- Too simple — wouldn't test pullback logic, ATR stops, or NO_SETUP states
- Crossover alone generates too many false signals — bad for learning risk interaction

**Why not something complex (e.g., ML)?**
- Violates project principles — not deterministic/transparent

## 3. Strategy Identity

| Field | Value |
|-------|-------|
| ID (slug) | `gold-trend-pullback` |
| Name | Gold Trend Pullback |
| Version | `1.0.0` |
| Instrument | XAU/USD |
| Primary Timeframe | **1h** |
| Directions | **LONG and SHORT** |
| Risk Profiles | Conservative, Balanced, Aggressive (all compatible) |

## 4. Concept

```
IF trend is UP (price > EMA200, EMA50 > EMA200):
  Wait for pullback to EMA20
  IF RSI confirms momentum (40-60 zone — not overbought):
    → BUY with ATR-based SL/TP

IF trend is DOWN (price < EMA200, EMA50 < EMA200):
  Wait for rally to EMA20
  IF RSI confirms (40-60 zone — not oversold):
    → SELL with ATR-based SL/TP

OTHERWISE:
  → HOLD (NO_SETUP or trend unclear)
```

## 5. Indicators

| Indicator | Period | Purpose |
|-----------|--------|---------|
| EMA 20 | 20 | Pullback/rally target |
| EMA 50 | 50 | Trend momentum |
| EMA 200 | 200 | Major trend filter |
| RSI | 14 | Momentum confirmation |
| ATR | 14 | Stop loss / take profit sizing |

All computed on **1h candles**, using data available up to current candle only.

## 6. Entry Conditions

### 6.1 LONG Entry

All conditions must be true on **current (latest complete) candle**:

| # | Condition | Rule |
|---|-----------|------|
| 1 | Major trend UP | `close > EMA200` |
| 2 | Momentum UP | `EMA50 > EMA200` |
| 3 | Pullback occurred | `low <= EMA20` AND `close > EMA20` (touched and bounced) |
| 4 | RSI confirmation | `RSI >= rsi_entry_min` AND `RSI <= rsi_entry_max` (default 40-60) |
| 5 | No open position | Checked by Risk Engine (not strategy) |
| 6 | Sufficient data | At least `min_candles_required` candles (default 200) |

**Signal reference (Candle N close):** Strategy uses candle N close for setup detection and SL/TP suggestions.

**Execution (default):** Fill at **Candle N+1 open** via `fill_timing = next_open` — not at N close.

**Suggested SL:** `close - (ATR × atr_sl_multiplier)` (default 1.5× ATR below signal close)

**Suggested TP:** `close + (ATR × atr_tp_multiplier)` (default 3.0× ATR above signal close)

**Risk/Reward ratio:** default 1:2 (SL=1.5 ATR, TP=3.0 ATR)

### 6.2 SHORT Entry

Mirror of LONG:

| # | Condition | Rule |
|---|-----------|------|
| 1 | Major trend DOWN | `close < EMA200` |
| 2 | Momentum DOWN | `EMA50 < EMA200` |
| 3 | Rally occurred | `high >= EMA20` AND `close < EMA20` (touched and rejected) |
| 4 | RSI confirmation | `RSI >= rsi_entry_min` AND `RSI <= rsi_entry_max` |
| 5 | Sufficient data | >= 200 candles |

**Suggested SL:** `close + (ATR × atr_sl_multiplier)`

**Suggested TP:** `close - (ATR × atr_tp_multiplier)`

## 7. Exit Conditions

### 7.1 Stop Loss (Primary)

Handled by execution layer — not strategy signal:
- LONG: price hits SL level
- SHORT: price hits SL level

### 7.2 Take Profit (Primary)

Handled by execution layer.

### 7.3 Strategy Exit (Trend Reversal)

Strategy emits **CLOSE** when:

**LONG position context** (strategy tracks via signal metadata, actual position check by system):
```
IF EMA50 crosses below EMA200 (was above, now below):
  → CLOSE signal, reason: "TREND_REVERSAL: EMA50 crossed below EMA200"
```

**SHORT position context:**
```
IF EMA50 crosses above EMA200:
  → CLOSE signal, reason: "TREND_REVERSAL: EMA50 crossed above EMA200"
```

**Note:** Strategy generates CLOSE based on indicator state. System verifies open position exists before executing.

### 7.4 End of Backtest

System closes open positions — not strategy responsibility.

## 8. HOLD / NO_SETUP States

| State | Reason String |
|-------|---------------|
| Insufficient data | `INSUFFICIENT_DATA: need {n} candles, have {m}` |
| No trend | `NO_SETUP: price below EMA200, no long setup` |
| Wrong trend direction | `NO_SETUP: downtrend active, no long setup` |
| No pullback | `NO_SETUP: waiting for pullback to EMA20` |
| RSI out of range | `NO_SETUP: RSI {value} outside entry zone [{min}-{max}]` |
| Already in trend but no entry trigger | `HOLD: trend valid, no pullback yet` |

Every evaluation **must** return a Signal with descriptive reason.

## 9. Parameters

### 9.1 Configurable (via parameter_overrides)

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| ema_fast | 20 | 10-30 | Pullback EMA |
| ema_slow | 50 | 30-100 | Momentum EMA |
| ema_trend | 200 | 100-300 | Trend filter EMA |
| rsi_period | 14 | 7-21 | RSI period |
| rsi_entry_min | 40 | 30-50 | Min RSI for entry |
| rsi_entry_max | 60 | 50-70 | Max RSI for entry |
| atr_period | 14 | 7-21 | ATR period |
| atr_sl_multiplier | 1.5 | 1.0-3.0 | SL distance in ATR |
| atr_tp_multiplier | 3.0 | 2.0-6.0 | TP distance in ATR |
| min_candles_required | 200 | 150-300 | Warmup period |

### 9.2 Fixed (in code v1.0.0)

| Rule | Fixed Value |
|------|-------------|
| Entry trigger | Pullback to EMA20 with bounce |
| Trend filter | EMA200 + EMA50 alignment |
| Exit on reversal | EMA50/EMA200 cross |
| Signal candle | Candle N close (setup detection) |
| Execution timing | Candle N+1 open (default next_open) |
| One position at a time | Yes (via Risk Engine) |
| Pyramiding | No |
| Session filter | None (all hours) |

Changing fixed rules → requires **v1.1.0 or v2.0.0**.

## 10. Market / Session Restrictions

| Restriction | v1.0.0 |
|-------------|--------|
| Trading hours | All available 1h candles |
| Weekend | No candles expected — strategy idle |
| News filter | None |
| Volatility filter | None (Risk Engine may add) |

## 11. Invalidation Rules

Setup invalidated (NO_SETUP) when:

1. Trend direction changes before entry triggers
2. Pullback exceeds EMA20 without bounce (closes wrong side for 3 consecutive candles — future enhancement, not v1.0.0)
3. RSI exits entry zone before bounce confirmation

v1.0.0 keeps invalidation simple — evaluated each candle independently.

## 12. Signal Metadata Example

```json
{
  "ema20": 2650.12,
  "ema50": 2640.55,
  "ema200": 2610.30,
  "rsi": 52.4,
  "atr": 8.35,
  "trend": "up",
  "pullback_detected": true,
  "effective_parameters": {
    "ema_fast": 20,
    "atr_sl_multiplier": 1.5
  }
}
```

## 13. Expected Behavior (Realistic)

| Aspect | Expectation |
|--------|-------------|
| Trade frequency | Low-medium (~2-8 trades/month on 1h) |
| Win rate | Unknown — likely 40-55% for trend systems |
| Drawdown periods | Expected — trend systems have losing streaks |
| Best use | Learning, infrastructure validation, parameter research |
| Profit guarantee | **None** |

## 14. Research Path (Post-v1.0.0)

After infrastructure works:

1. Backtest 1-3 years of data
2. Analyze by session, day of week
3. Tune parameters (creates v1.1.0 with documented changes)
4. Compare Conservative vs Aggressive profiles
5. Out-of-sample test on recent 3 months

## 15. Version History (Planned)

| Version | Changes |
|---------|---------|
| 1.0.0 | Initial — as specified here |
| 1.1.0 | Parameter tuning based on backtest (future) |
| 2.0.0 | Logic changes e.g., add session filter (future) |

## 16. Cross-References

- Framework: `05_STRATEGY_FRAMEWORK.md`
- Risk sizing: `06_RISK_ENGINE.md`
- Backtest: `08_BACKTESTING_SPEC.md`
- Analytics: `12_ANALYTICS_METRICS.md`
