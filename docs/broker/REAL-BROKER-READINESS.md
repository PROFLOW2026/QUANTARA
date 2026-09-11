# Real Broker Readiness

## What Paper Models Today (QUANTARA_STANDARD_PAPER)

| Capability | Status |
|------------|--------|
| Single shared broker account ($320,000) | Implemented |
| Strategy portfolios as attribution only | Implemented |
| Pre-trade broker check | Implemented |
| Buying power / initial margin / free margin | Implemented |
| Gross & net leverage caps (2× default) | Implemented |
| Asset-class margin rules | Implemented |
| NETTING position mode | Implemented |
| HEDGING architecture | Implemented (mode switch) |
| Order rejection audit log | Implemented |
| JPY→USD conversion | Implemented |
| Stale-data entry block | Preserved + broker layer |
| Gap-through-stop exits | Existing exit_triggers (US RTH) |

## Paper Assumptions (Not Named Broker)

- US equities: 50% initial margin, 25% maintenance, 2× max leverage
- Forex: 5% initial margin (20×), shorting allowed
- Crypto: 100% cash (spot), no short unless profile changed
- Gold/commodity: 10% initial margin
- Max gross account leverage: 2.0×
- Default position mode: NETTING

## What Future RealBrokerAdapter Must Provide

- `get_account()`, `get_positions()`, `get_buying_power()`
- `submit_order()`, `cancel_order()`, `get_order()`, `get_fills()`
- Broker-specific margin tables, short locate, order min/step
- External reconciliation vs internal ledger

## Strategy-Independent Layers

Strategy engine, opportunity keys, and risk-to-SL guards unchanged. Broker layer is the only gate for account reality.

## Pre-Live Checklist

- [ ] Select live broker and map to BrokerProfile override
- [ ] Owner-approved clean Paper reset (`quantara_paper_competition` account)
- [ ] Replay historical entries under new rules
- [ ] Verify reconciliation $0.00 after reset
- [ ] Connect RealBrokerAdapter in staging
- [ ] Compare broker-reported vs internal for 1 week paper

## Legacy Data

Competition run before broker rebuild = **LEGACY_SIMULATION**. Not converted. Preserved for research only.
