# 2026-09-11 Broker Replay (QUANTARA_STANDARD_PAPER)

Read-only replay. Historical DB unchanged.

- Total entries replayed: **274**
- Would accept: **191**
- Would reject: **83**

## Rejection reasons

- max_gross_leverage: 73
- short_not_allowed: 10

## Sample rejections

- BTCUSD qty=0.0067 @ 2026-09-11 16:00:00+00:00: short_not_allowed — shorting not allowed for crypto
- BTCUSD qty=0.0135 @ 2026-09-11 16:00:00+00:00: short_not_allowed — shorting not allowed for crypto
- BTCUSD qty=0.027 @ 2026-09-11 16:00:00+00:00: short_not_allowed — shorting not allowed for crypto
- BTCUSD qty=0.0404 @ 2026-09-11 16:00:00+00:00: short_not_allowed — shorting not allowed for crypto
- BTCUSD qty=0.0539 @ 2026-09-11 16:00:00+00:00: short_not_allowed — shorting not allowed for crypto
- TSLA qty=14.0 @ 2026-09-11 17:30:00+00:00: max_gross_leverage — projected gross leverage 2.0114 > max 2.0
- AMD qty=3.0 @ 2026-09-11 17:30:00+00:00: max_gross_leverage — projected gross leverage 2.0015 > max 2.0
- AMD qty=5.0 @ 2026-09-11 17:30:00+00:00: max_gross_leverage — projected gross leverage 2.0047 > max 2.0
- AMD qty=7.0 @ 2026-09-11 17:30:00+00:00: max_gross_leverage — projected gross leverage 2.0079 > max 2.0
- NVDA qty=8.0 @ 2026-09-11 17:40:00+00:00: max_gross_leverage — projected gross leverage 2.0021 > max 2.0
- NVDA qty=17.0 @ 2026-09-11 17:40:00+00:00: max_gross_leverage — projected gross leverage 2.0082 > max 2.0
- NVDA qty=34.0 @ 2026-09-11 17:40:00+00:00: max_gross_leverage — projected gross leverage 2.0198 > max 2.0
- NVDA qty=52.0 @ 2026-09-11 17:40:00+00:00: max_gross_leverage — projected gross leverage 2.0321 > max 2.0
- NVDA qty=69.0 @ 2026-09-11 17:40:00+00:00: max_gross_leverage — projected gross leverage 2.0437 > max 2.0
- XAUUSD qty=0.26 @ 2026-09-11 17:45:00+00:00: max_gross_leverage — projected gross leverage 2.0002 > max 2.0