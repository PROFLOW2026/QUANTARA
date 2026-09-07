# QUANTARA — Strategy Framework

## 1. Purpose

מגדיר את **חוזה Strategy** — הממשק, ה-registry, versioning, וכללי הרחבה.
כל Strategy היא מודול עצמאי שניתן להוסיף **בלי לשנות את הליבה**.

## 2. Design Principles

1. **Mode agnostic** — Strategy לא יודעת Backtest/Paper/Live
2. **Pure functions** — input: candles + context → output: Signal
3. **No side effects** — no DB, no orders, no portfolio access
4. **Versioned** — שינוי = version חדש, never in-place edit
5. **Testable** — unit testable with mock candles
6. **Deterministic** — same input → same output (given same parameters)

## 3. Strategy Contract (Python Interface)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from decimal import Decimal

class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    MODIFY = "modify"

@dataclass(frozen=True)
class Signal:
    action: SignalAction
    reason: str
    confidence: Optional[Decimal] = None
    suggested_sl: Optional[Decimal] = None
    suggested_tp: Optional[Decimal] = None
    metadata: Optional[dict] = None

@dataclass(frozen=True)
class StrategyContext:
    instrument_id: str
    timeframe: str
    parameters: dict
    # NO portfolio, NO mode, NO broker

class BaseStrategy(ABC):
    """All strategies must implement this contract."""

    @classmethod
    @abstractmethod
    def strategy_id(cls) -> str:
        """Unique slug, e.g. 'gold-trend-pullback'"""
        ...

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        """Semver, e.g. '1.0.0'"""
        ...

    @classmethod
    @abstractmethod
    def name(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def description(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def supported_instruments(cls) -> list[str]:
        ...

    @classmethod
    @abstractmethod
    def supported_timeframes(cls) -> list[str]:
        ...

    @classmethod
    @abstractmethod
    def default_parameters(cls) -> dict:
        ...

    @classmethod
    @abstractmethod
    def parameters_schema(cls) -> dict:
        """JSON Schema for parameter validation."""
        ...

    @classmethod
    @abstractmethod
    def risk_profile_compatibility(cls) -> list[str]:
        """conservative, balanced, aggressive"""
        ...

    @abstractmethod
    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        """
        Evaluate strategy on provided candles.
        
        Rules:
        - candles sorted ascending by timestamp
        - candles include ONLY past and current (no future)
        - minimum candle count must be checked internally
        - return HOLD with reason if insufficient data
        """
        ...
```

## 4. Strategy Registry

```python
# Conceptual structure — apps/engine/strategies/registry.py

STRATEGY_REGISTRY: dict[str, dict[str, type[BaseStrategy]]] = {
    "gold-trend-pullback": {
        "1.0.0": GoldTrendPullbackV1,
        # "1.1.0": GoldTrendPullbackV1_1,  # future
    },
}
```

### 4.1 Registry Operations

| Operation | Description |
|-----------|-------------|
| `register(strategy_class)` | Add to registry at startup |
| `get(slug, version)` | Load specific version |
| `list_all()` | All strategies with versions |
| `get_latest(slug)` | Latest active version |
| `validate_parameters(slug, version, params)` | JSON Schema validation |

### 4.2 Registration Flow

```
1. Developer creates new strategy module in apps/engine/strategies/
2. Implements BaseStrategy contract
3. Registers in registry.py
4. Creates DB record: strategies + strategy_versions (via admin/seed)
5. Strategy available for StrategyInstance creation
```

## 5. Strategy Metadata (Database)

Each strategy version stored in DB (see `04_DATABASE_MODEL.md`):

| Field | Source |
|-------|--------|
| ID | UUID (DB) |
| slug | strategy_id from class |
| version | version from class |
| name, description | from class |
| supported_instruments | from class |
| supported_timeframes | from class |
| parameters | default_parameters merged with overrides |
| parameters_schema | from class |
| risk_profile_compatibility | from class |
| logic_hash | SHA256 of strategy source file |
| status | draft → active → deprecated |
| created_at | timestamp |

## 6. Versioning Rules

### 6.1 When to Bump Version

| Change Type | Version Bump |
|-------------|--------------|
| Bug fix (same logic intent) | PATCH (1.0.0 → 1.0.1) |
| Parameter default change | MINOR (1.0.0 → 1.1.0) |
| Logic change (entry/exit rules) | MINOR or MAJOR |
| Complete rewrite | MAJOR (1.x → 2.0.0) |

### 6.2 Immutability

- Once a `strategy_version` has any signal/trade/backtest → **frozen**
- Changes require new version row
- Old versions remain queryable for historical analysis
- UI shows version on every trade and backtest

### 6.3 Naming Convention

```
{InstrumentHint}{StrategyType}{Version}
Examples:
  GoldTrendPullback v1.0.0
  GoldTrendPullback v1.1.0
  GoldTrendPullback v2.0.0
```

## 7. Signal Output Specification

### 7.1 Actions

| Action | Meaning | Next Step |
|--------|---------|-----------|
| BUY | Open long (or close short + open long) | → Risk Engine |
| SELL | Open short (or close long + open short) | → Risk Engine |
| HOLD | No action | → Decision log only |
| CLOSE | Close existing position | → Execution |
| MODIFY | Change SL/TP on open position | → Risk/Execution |

### 7.2 Required Fields

| Field | Required | Notes |
|-------|----------|-------|
| action | ✅ | |
| reason | ✅ | Always explain — critical for Decision Log |
| confidence | ❌ | 0.0–1.0 if provided |
| suggested_sl | ❌ | Required for BUY/SELL (Risk validates) |
| suggested_tp | ❌ | Optional |
| metadata | ❌ | Indicator values for debugging/analytics |

### 7.3 Example Signals

```python
# No setup
Signal(action=HOLD, reason="NO_SETUP: price below EMA200, no long allowed")

# Valid long entry
Signal(
    action=BUY,
    reason="Pullback to EMA20 in uptrend, RSI confirmed",
    confidence=Decimal("0.72"),
    suggested_sl=Decimal("2645.50"),
    suggested_tp=Decimal("2678.00"),
    metadata={"ema20": 2650.1, "ema50": 2640.0, "rsi": 55.2, "atr": 8.5}
)

# Insufficient data
Signal(action=HOLD, reason="INSUFFICIENT_DATA: need 200 candles, have 150")
```

## 8. Parameter System

### 8.1 Structure

```json
{
  "ema_fast": 20,
  "ema_slow": 50,
  "ema_trend": 200,
  "rsi_period": 14,
  "rsi_entry_min": 40,
  "rsi_entry_max": 60,
  "atr_period": 14,
  "atr_sl_multiplier": 1.5,
  "atr_tp_multiplier": 3.0,
  "min_candles_required": 200
}
```

### 8.2 Override Hierarchy

```
Strategy default_parameters
  → strategy_version.parameters (DB, frozen at version creation)
    → strategy_instance.parameter_overrides (runtime config)
```

Effective parameters = merge(default, version, instance) — logged on each signal.

## 9. Strategy Instance Lifecycle

```
1. User selects Strategy + Version + Instrument + Timeframe + Risk Profile
2. System creates strategy_instance linked to portfolio
3. Worker picks up active instances on each candle
4. Instance can be deactivated (is_active=false) — no deletion if has history
5. Parameter overrides can change — logged, but version stays same
   (override changes don't create new version — only code changes do)
```

## 10. File Structure

```
apps/engine/strategies/
├── __init__.py
├── base.py              # BaseStrategy, Signal, Context
├── registry.py          # STRATEGY_REGISTRY
├── gold_trend_pullback/
│   ├── __init__.py
│   ├── v1_0_0.py        # GoldTrendPullbackV1
│   └── indicators.py    # shared helpers (optional)
└── tests/
    └── test_gold_trend_pullback_v1.py
```

## 11. Adding a New Strategy (Checklist)

- [ ] Create module implementing BaseStrategy
- [ ] Register in registry.py
- [ ] Write unit tests with fixture candles
- [ ] Document in strategy-specific md (optional)
- [ ] Seed DB strategy + strategy_version
- [ ] Verify Backtest + Paper use same class
- [ ] Verify Decision Log captures all signal types

## 12. Anti-Patterns (Forbidden)

| Anti-Pattern | Why |
|--------------|-----|
| `if mode == "backtest"` in strategy | Breaks parity |
| Direct DB queries in strategy | Side effects |
| Position sizing in strategy | Risk Engine responsibility |
| Hardcoded instrument prices | Use candles |
| Mutating input candles | Immutability |
| Random without seed in backtest | Breaks reproducibility |

## 13. Cross-References

- First strategy spec: `10_GOLD_FIRST_STRATEGY.md`
- Risk interaction: `06_RISK_ENGINE.md`
- Flow: `03_CANONICAL_TRADING_FLOW.md`
- DB: `04_DATABASE_MODEL.md`
