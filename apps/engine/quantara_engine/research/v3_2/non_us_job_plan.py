"""Asset-class-specific V3.2 job matrix for non-US profit discovery."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _test_pairs
from quantara_engine.research.v3_2.job_plan import V32Job, job_key

CRYPTO = frozenset({"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"})
GOLD = frozenset({"XAUUSD"})
FX_CORE = frozenset({"GBPJPY", "EURUSD", "USDJPY"})
FX_DEFER = frozenset({"GBPUSD", "AUDUSD"})

WAVE1_SPECS: list[dict[str, Any]] = [
    {
        "track": "crypto",
        "family": "donchian_breakout_v2",
        "assets": CRYPTO,
        "timeframes": ("5m", "15m", "1h"),
        "variants": [{"channel": 20, "atr_sl": 1.2, "atr_tp": 2.0}, {"channel": 40, "atr_sl": 1.5, "atr_tp": 2.5}],
    },
    {
        "track": "crypto",
        "family": "crypto_atr_expansion_break",
        "assets": CRYPTO,
        "timeframes": ("15m", "1h"),
        "variants": [{"breakout": 20, "atr_mult": 1.25}, {"breakout": 30, "atr_mult": 1.35}],
    },
    {
        "track": "crypto",
        "family": "atr_trailing_trend",
        "assets": CRYPTO,
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_slow": 34, "atr_sl": 1.4, "atr_tp": 2.2}, {"ema_slow": 55, "atr_sl": 1.6, "atr_tp": 2.8}],
    },
    {
        "track": "crypto",
        "family": "vol_expansion_v2",
        "assets": CRYPTO,
        "timeframes": ("15m",),
        "variants": [{"bb_std": 2.0}, {"bb_std": 2.6}],
    },
    {
        "track": "crypto",
        "family": "mtf_trend_ltf_entry",
        "assets": frozenset({"BTCUSD", "ETHUSD"}),
        "timeframes": ("5m",),
        "variants": [{"ema_slow": 50}, {"ema_slow": 80}],
    },
    {
        "track": "crypto",
        "family": "momentum_after_base",
        "assets": CRYPTO,
        "timeframes": ("15m",),
        "variants": [{}],
    },
    {
        "track": "gold",
        "family": "fx_asian_london_break",
        "assets": GOLD,
        "timeframes": ("15m", "1h"),
        "variants": [{"asian_end_hour_utc": 7}, {"asian_end_hour_utc": 8}],
    },
    {
        "track": "gold",
        "family": "xau_overlap_momentum",
        "assets": GOLD,
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_slow": 34}, {"ema_slow": 55, "mom_thr": 0.003}],
    },
    {
        "track": "gold",
        "family": "donchian_breakout_v2",
        "assets": GOLD,
        "timeframes": ("15m", "1h"),
        "variants": [{"channel": 30}, {"channel": 55}],
    },
    {
        "track": "gold",
        "family": "channel_mean_revert",
        "assets": GOLD,
        "timeframes": ("15m", "1h"),
        "variants": [{"channel": 25, "dev": 0.005}, {"channel": 30, "dev": 0.008}],
    },
    {
        "track": "fx",
        "family": "fx_asian_london_break",
        "assets": FX_CORE,
        "timeframes": ("15m", "1h"),
        "variants": [{"asian_end_hour_utc": 7}, {"asian_end_hour_utc": 8}],
    },
    {
        "track": "fx",
        "family": "prev_day_hl_break",
        "assets": FX_CORE,
        "timeframes": ("15m", "1h"),
        "variants": [{"atr_sl": 1.4, "atr_tp": 2.2}],
    },
    {
        "track": "fx",
        "family": "atr_trailing_trend",
        "assets": FX_CORE,
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_slow": 55}, {"ema_slow": 80}],
    },
    {
        "track": "fx",
        "family": "channel_mean_revert",
        "assets": FX_CORE,
        "timeframes": ("15m",),
        "variants": [{"channel": 25, "dev": 0.004}, {"channel": 30, "dev": 0.006}],
    },
]

WAVE2_SPECS: list[dict[str, Any]] = [
    {
        "track": "crypto",
        "family": "squeeze_release",
        "assets": CRYPTO,
        "timeframes": ("15m", "1h"),
        "variants": [{}],
    },
    {
        "track": "crypto",
        "family": "ema_pullback_continue",
        "assets": frozenset({"BTCUSD", "ETHUSD"}),
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_fast": 8, "ema_slow": 21}, {}],
    },
    {
        "track": "crypto",
        "family": "rsi_divergence_mr",
        "assets": CRYPTO,
        "timeframes": ("15m",),
        "variants": [{}],
    },
    {
        "track": "gold",
        "family": "atr_trailing_trend",
        "assets": GOLD,
        "timeframes": ("5m", "15m"),
        "variants": [{"ema_slow": 55}],
    },
    {
        "track": "fx",
        "family": "donchian_breakout_v2",
        "assets": FX_CORE,
        "timeframes": ("15m", "1h"),
        "variants": [{"channel": 30}, {"channel": 55}],
    },
]


def _fx_research_allowed(store: TradingStore, symbol: str) -> bool:
    if symbol not in FX_CORE:
        return symbol not in FX_DEFER
    inst = store.get_instrument_by_symbol(symbol)
    if not inst:
        return False
    row = store.session.execute(
        text(
            """
            SELECT MAX(timestamp) FROM candles
            WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = '5m'
            """
        ),
        {"iid": str(inst.id)},
    ).scalar()
    if row is None:
        return False
    from datetime import datetime, timezone

    ts = row.replace(tzinfo=timezone.utc) if row.tzinfo is None else row
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    return age_h <= 72


def plan_non_us_jobs(store: TradingStore, *, wave: int = 1) -> list[V32Job]:
    specs = WAVE1_SPECS if wave == 1 else WAVE2_SPECS if wave == 2 else []
    quality = build_data_quality_matrix(store)
    allowed = set(_test_pairs(quality))
    raw: list[V32Job] = []
    idx = 0
    for spec in specs:
        for vi, params in enumerate(spec["variants"], start=1):
            variant_id = f"v{vi}"
            for asset in sorted(spec["assets"]):
                if asset in FX_CORE | FX_DEFER and not _fx_research_allowed(store, asset):
                    continue
                for tf in spec["timeframes"]:
                    if (asset, tf) not in allowed:
                        continue
                    instrument = store.get_instrument_by_symbol(asset)
                    if not instrument:
                        continue
                    n = store.session.execute(
                        text(
                            """
                            SELECT COUNT(*)::int FROM candles
                            WHERE instrument_id = CAST(:iid AS uuid)
                              AND timeframe = :tf AND timestamp >= :start
                            """
                        ),
                        {"iid": str(instrument.id), "tf": tf, "start": V3_RESEARCH_START},
                    ).scalar()
                    if not n or int(n) < 250:
                        continue
                    for direction in ("long", "short"):
                        key = job_key(spec["family"], variant_id, asset, tf, direction)
                        raw.append(
                            V32Job(
                                index=idx,
                                key=key,
                                family=spec["family"],
                                variant_id=variant_id,
                                asset=asset,
                                timeframe=tf,
                                direction=direction,
                                parameters=dict(params),
                            )
                        )
                        idx += 1
    raw.sort(key=lambda j: (j.asset, j.family, j.key))
    return raw
