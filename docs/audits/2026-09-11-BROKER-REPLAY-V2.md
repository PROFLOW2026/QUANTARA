# 2026-09-11 Broker Replay V2 (full lifecycle)

Simulates entries **and** exits sequentially through QUANTARA_STANDARD_PAPER.
Read-only — historical DB unchanged.

- Original entries: **274**
- Accepted: **247**
- Rejected: **27**
- Exits replayed: **140**

## End state

- Ending balance: **$215,214.70**
- Ending equity: **$215,615.60**
- Open broker positions: **7**
- Realized P&L: **$-104,785.30**
- Max gross exposure: **$798,059.04**
- Max initial margin used: **$133,862.94**

## Entry rejection reasons

- max_gross_leverage: 15
- short_not_allowed: 10
- max_asset_exposure: 2