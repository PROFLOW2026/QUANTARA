# Real Broker Readiness

## Canonical Execution Truth (Post-Closure)

| Layer | Source of truth |
|-------|-----------------|
| Broker account | `broker_accounts` table |
| Broker positions | `broker_positions` (NETTING only) |
| Broker orders | `broker_orders` with DB idempotency |
| Broker fills | `broker_fills` — every execution |
| Strategy legs | Attribution only via `broker_attribution_ledger` |

**Final broker validation runs at execution time** (N+1 open), not at signal creation.

## Position Mode

**NETTING ONLY** at runtime. Schema `UNIQUE(broker_account_id, instrument_id)` supports netting.
HEDGING is **not** implemented — do not enable until schema/runtime supports simultaneous long+short.

## QUANTARA_STANDARD_PAPER Assumptions

| Asset | Model |
|-------|--------|
| US equities | 50% initial / 25% maintenance margin, RTH entries |
| Forex | 5% initial margin, 24×5 |
| Gold | 10% initial margin |
| **BTCUSD / ETHUSD** | **SPOT crypto** — 100% cash, **SHORT signals broker-rejected** |
| Account | $320,000 shared; 160×$2k = reference allocation only |

## Account Semantics

- **Balance** = starting cash + realized P&L (margin reserve does not reduce balance)
- **Equity** = balance + unrealized P&L
- **Available margin** = equity − initial margin used (shown in UI; not universal "buying power")
- **Spot crypto cash** = unallocated cash for BTC/ETH spot purchases

## Risk-Reducing Orders

Closes/reductions are allowed in margin call. Closes correctly reduce gross/net/margin.
Flips validate the opening remainder separately.

## Future RealBrokerAdapter

- `get_account()`, `get_positions()`, `submit_order()`, `get_fills()`
- Broker-specific margin, short locate, partial fills
- External reconciliation

## Owner Actions Before Live Paper Reset

1. Review migration `0006_broker_account.sql` (revised)
2. Apply migration when ready
3. Approve clean reset (`scripts/paper_broker_reset.py --dry-run`)
4. Start runtime manually
