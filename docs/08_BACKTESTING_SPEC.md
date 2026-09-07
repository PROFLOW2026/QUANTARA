# QUANTARA — Backtesting Specification

## 1. Purpose

Backtesting Engine מריץ **סימולציה היסטורית reproducible** עם:

- Candle-by-candle processing
- No lookahead bias
- Same Strategy + Risk + Execution + P&L model as Paper
- Full metrics on completion

## 2. Core Requirements

| Requirement | Implementation |
|-------------|----------------|
| Candle-by-candle | Sequential loop, one candle at a time |
| No lookahead | Strategy sees candles[:i+1] only; execution at i+1 open uses only known data |
| Same strategy logic | Identical Strategy class as Paper |
| Same risk rules | Identical RiskEngine as Paper |
| Same fill/P&L model | fill_price includes spread+slippage; net = gross - fees |
| Default fill_timing | **next_open** (same as Paper) |
| Reproducible | Same inputs → identical outputs |
| Immutable results | BacktestRun + trades stored permanently |

## 3. Backtest Lifecycle

```
1. User configures backtest (UI)
2. System creates backtest_runs + virtual portfolio
3. Worker loads candles for range
4. Pre-compute dataset_fingerprint from OHLCV content
5. Candle loop (see §4)
6. Close remaining positions at last candle (exit_reason: end_of_backtest)
7. Compute metrics → status: completed
```

## 4. Candle Loop (Canonical — matches Paper)

For each candle index `i`:

```
1. EXECUTE pending intents at candles[i].open
   (queued from approved signals on candle i-1)

2. CHECK SL/TP on candles[i] OHLC

3. UPDATE unrealized P&L (mark at candles[i].close)

4. STRATEGY evaluates on candles[0..i] → Signal → Decision log

5. RISK evaluates → if approved:
   - Entry/CLOSE: queue intent for candles[i+1].open (if i+1 exists)
   - Store target_risk_amount + actual_risk_amount on intent

6. SNAPSHOT portfolio (optional: daily or per trade)
```

**No future leakage:** Step 4 never reads candles[i+1]. Step 1 only uses candles[i].open for fills queued from past signals.

Last candle: pending intents that would execute beyond range → close at last candle close (end_of_backtest) or expire per config.

## 5. SimulatedBrokerAdapter

Identical interface and fill logic to PaperBrokerAdapter.

```python
class SimulatedBrokerAdapter(BrokerAdapter):
    def __init__(self, execution_assumptions: ExecutionAssumptions):
        self.spread = execution_assumptions.spread
        self.slippage_pct = execution_assumptions.slippage_pct
        self.fee_rate = execution_assumptions.fee_rate
        self.fill_timing = execution_assumptions.fill_timing  # default: next_open
```

**Default execution assumptions (XAUUSD):**

```json
{
  "spread": 0.30,
  "slippage_pct": 0.0001,
  "fee_rate": 0,
  "fill_timing": "next_open"
}
```

`candle_close` — optional research override only, not default.

Stored in `backtest_runs.execution_assumptions` — immutable after run starts.

## 6. No Lookahead Rules

### 6.1 Data Access

```python
# CORRECT — strategy on candle i
candles_for_strategy = all_candles[:i + 1]

# CORRECT — execution at i+1 open (when processing candle i+1)
fill_base = all_candles[i + 1].open  # only when i+1 is current step

# FORBIDDEN — strategy reading future
next_close = all_candles[i + 1].close  # in strategy.evaluate()
```

### 6.2 Clock

```python
def current_candle_time(self) -> datetime:
    return self._current_candle.timestamp  # open time of current candle
```

### 6.3 Validation Tests

- Future candle mutation doesn't change past decisions
- Removing future candles doesn't change past decisions
- Signal on N never fills at N close under default next_open

## 7. BacktestRun Metrics

Computed on completion, stored in `backtest_runs.metrics` (JSONB):

| Metric | Formula / Description |
|--------|----------------------|
| initial_capital | Input |
| final_capital | Portfolio equity at end |
| total_return_pct | (final - initial) / initial × 100 |
| total_trades | Count |
| winning_trades | realized_pnl > 0 |
| losing_trades | realized_pnl <= 0 |
| win_rate | winning / total × 100 |
| average_win | mean(realized_pnl where > 0) |
| average_loss | mean(realized_pnl where <= 0) — **signed (negative)** |
| profit_factor | sum(wins) / abs(sum(losses)) |
| maximum_drawdown_pct | Max peak-to-trough |
| best_trade | max(realized_pnl) |
| worst_trade | min(realized_pnl) |
| average_duration_seconds | mean(duration) |
| total_fees | sum(fees) |
| total_slippage | sum(slippage attribution) |
| total_spread | sum(spread attribution) |
| expectancy | (win_rate × avg_win) + (loss_rate × avg_loss) — avg_loss signed |
| strategy_version | Reference |
| parameters | Snapshot |
| dataset_fingerprint | SHA256 of OHLCV content |
| execution_assumptions | Snapshot |

## 8. Dataset Fingerprint (Reproducibility)

Metadata-only hashes are **insufficient** — OHLC can change without count/timestamp changes.

**Canonical fingerprint:**

```python
def compute_dataset_fingerprint(candles: list[Candle]) -> str:
    """
    Deterministic hash of actual candle content.
    Candles sorted ascending by timestamp before hashing.
    """
    lines = []
    for c in sorted(candles, key=lambda x: x.timestamp):
        vol = c.volume if c.volume is not None else ""
        lines.append(
            f"{c.timestamp.isoformat()}|{c.open}|{c.high}|{c.low}|{c.close}|{vol}"
        )
    content = "\n".join(lines)
    return sha256(content.encode()).hexdigest()
```

Stored in `backtest_runs.dataset_fingerprint`.

**Rule:** same fingerprint = same historical candle content (for that instrument/timeframe/range loaded).

Optional: store `candle_count`, `start_date`, `end_date` as metadata alongside fingerprint (not as substitute).

## 9. Equity Curve Data

Portfolio snapshots during backtest — minimum: start, each trade close, end.

## 10. Out-of-Sample / Walk-Forward Foundation

Fields in `backtest_runs.parameters` for future use — not Phase 1 UI.

## 11. Backtest vs Paper Consistency

| Check | Method |
|-------|--------|
| Same strategy class | Registry lookup |
| Same risk engine | Shared module |
| Same fill_timing default | next_open |
| Same P&L formulas | Shared fill_calculator + pnl module |
| Same SL/TP logic | Shared exit checker |

**Acceptance test:** Same candle set + params → identical signals and trades (offline paper sim vs backtest).

## 12. Performance Targets

- 1 year 1h (~8760 candles): < 60 seconds
- 1 year 15m: < 5 minutes

## 13. Cross-References

- Flow: `03_CANONICAL_TRADING_FLOW.md`
- Paper: `07_PAPER_TRADING_ENGINE.md`
- Analytics: `12_ANALYTICS_METRICS.md`
- DB: `04_DATABASE_MODEL.md`
- Testing: `17_TESTING_AND_ACCEPTANCE.md`
