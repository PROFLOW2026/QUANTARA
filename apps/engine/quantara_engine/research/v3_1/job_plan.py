"""V3.1 Stage A job matrix (~300–400 jobs, long/short separate)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _test_pairs

_EQUITIES = frozenset({"NVDA", "TSLA", "AMD", "COIN"})
_CRYPTO = frozenset({"BTCUSD", "ETHUSD"})
_FX_METAL = frozenset({"XAUUSD", "GBPJPY"})

FAMILY_SPECS: list[dict[str, Any]] = [
    {
        "family": "donchian_channel",
        "label": "Donchian / channel trend",
        "assets": _CRYPTO | _FX_METAL | _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"channel": 20}, {"channel": 55}],
    },
    {
        "family": "atr_trend",
        "label": "ATR trend following",
        "assets": _CRYPTO | _FX_METAL | _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_fast": 15, "ema_slow": 40}, {"ema_fast": 20, "ema_slow": 50}],
    },
    {
        "family": "vol_expansion",
        "label": "Volatility expansion breakout",
        "assets": _CRYPTO | _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"bb_std": 2.0}, {"bb_std": 2.5}],
    },
    {
        "family": "mtf_trend",
        "label": "Multi-timeframe trend alignment",
        "assets": _CRYPTO | _FX_METAL,
        "timeframes": ("15m", "1h"),
        "variants": [{"ema_slow": 50}, {"ema_slow": 80}],
    },
    {
        "family": "session_breakout",
        "label": "Session breakout",
        "assets": _EQUITIES | {"XAUUSD"},
        "timeframes": ("5m", "15m"),
        "variants": [{}, {"max_bars": 18}],
    },
    {
        "family": "vwap_mean_reversion",
        "label": "VWAP mean reversion (equities)",
        "assets": _EQUITIES,
        "timeframes": ("5m", "15m"),
        "variants": [{"dev": 0.003}, {"dev": 0.005}],
    },
    {
        "family": "relative_strength",
        "label": "Relative-strength momentum",
        "assets": _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"mom_bars": 8}, {"mom_bars": 12}],
    },
    {
        "family": "momentum_vol_filter",
        "label": "Momentum + volatility filter",
        "assets": _CRYPTO | _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"atr_cap": 0.018}, {"atr_cap": 0.025}],
    },
    {
        "family": "range_atr_breakout",
        "label": "Range breakout + ATR confirm",
        "assets": _FX_METAL | _CRYPTO,
        "timeframes": ("15m", "1h"),
        "variants": [{"range": 12}, {"range": 20}],
    },
    {
        "family": "trend_pullback_alt",
        "label": "Pullback in established trend (alt rules)",
        "assets": _CRYPTO | _FX_METAL | _EQUITIES,
        "timeframes": ("15m", "1h"),
        "variants": [{"rsi_pull": 40}, {"rsi_pull": 45, "ema_slow": 80}],
    },
]


@dataclass(frozen=True)
class V31Job:
    index: int
    key: str
    family: str
    variant_id: str
    asset: str
    timeframe: str
    direction: str
    parameters: dict[str, Any]


def job_key(family: str, variant_id: str, asset: str, timeframe: str, direction: str) -> str:
    return f"{family}|{variant_id}|{asset}|{timeframe}|{direction}"


def plan_v31_jobs(store: TradingStore) -> list[V31Job]:
    quality = build_data_quality_matrix(store)
    allowed = set(_test_pairs(quality))
    raw: list[V31Job] = []
    idx = 0
    for spec in FAMILY_SPECS:
        family = spec["family"]
        for vi, params in enumerate(spec["variants"], start=1):
            variant_id = f"v{vi}"
            for asset in sorted(spec["assets"]):
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
                              AND timeframe = :tf
                              AND timestamp >= :start
                            """
                        ),
                        {"iid": str(instrument.id), "tf": tf, "start": V3_RESEARCH_START},
                    ).scalar()
                    if not n or int(n) < 250:
                        continue
                    for direction in ("long", "short"):
                        key = job_key(family, variant_id, asset, tf, direction)
                        raw.append(
                            V31Job(
                                index=idx,
                                key=key,
                                family=family,
                                variant_id=variant_id,
                                asset=asset,
                                timeframe=tf,
                                direction=direction,
                                parameters=dict(params),
                            )
                        )
                        idx += 1
    raw.sort(key=lambda j: j.key)
    out: list[V31Job] = []
    for i, j in enumerate(raw):
        out.append(
            V31Job(
                index=i,
                key=j.key,
                family=j.family,
                variant_id=j.variant_id,
                asset=j.asset,
                timeframe=j.timeframe,
                direction=j.direction,
                parameters=j.parameters,
            )
        )
    return out
