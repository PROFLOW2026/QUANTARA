"""V3.2 job matrix (~300–500 jobs, broker-aware Stage A)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _test_pairs

_EQUITIES = frozenset({"NVDA", "TSLA", "AMD", "COIN"})
_CRYPTO = frozenset({"BTCUSD", "ETHUSD"})
_FX = frozenset({"XAUUSD", "GBPJPY"})
_ALL = frozenset(list_active_db_symbols())

FAMILY_SPECS: list[dict[str, Any]] = [
    {"family": "donchian_breakout_v2", "assets": _ALL, "timeframes": ("15m", "1h"), "variants": [{"channel": 30}, {"channel": 55}]},
    {"family": "atr_trailing_trend", "assets": _ALL, "timeframes": ("15m",), "variants": [{"ema_slow": 55}, {"ema_slow": 80}]},
    {"family": "ema_pullback_continue", "assets": _EQUITIES | _CRYPTO, "timeframes": ("15m", "1h"), "variants": [{}, {"ema_fast": 8, "ema_slow": 21}]},
    {"family": "vol_expansion_v2", "assets": _CRYPTO | _EQUITIES, "timeframes": ("15m",), "variants": [{"bb_std": 2.2}, {"bb_std": 2.6}]},
    {"family": "squeeze_release", "assets": _CRYPTO | _EQUITIES, "timeframes": ("15m", "1h"), "variants": [{}]},
    {"family": "vwap_session_revert", "assets": _EQUITIES, "timeframes": ("5m", "15m"), "variants": [{"dev": 0.004}, {"dev": 0.006}]},
    {"family": "orb_continuation", "assets": _EQUITIES, "timeframes": ("5m", "15m"), "variants": [{}]},
    {"family": "prev_day_hl_break", "assets": _EQUITIES | _FX, "timeframes": ("15m", "1h"), "variants": [{}]},
    {"family": "mtf_trend_ltf_entry", "assets": _CRYPTO | _EQUITIES, "timeframes": ("5m",), "variants": [{"ema_slow": 50}, {"ema_slow": 80}]},
    {"family": "rsi_divergence_mr", "assets": _ALL, "timeframes": ("15m",), "variants": [{}]},
    {"family": "momentum_after_base", "assets": _CRYPTO | _EQUITIES, "timeframes": ("15m",), "variants": [{}]},
    {"family": "channel_mean_revert", "assets": _FX | _EQUITIES, "timeframes": ("15m", "1h"), "variants": [{"channel": 25}, {"dev": 0.008, "channel": 30}]},
]

MAX_JOBS = 500


@dataclass(frozen=True)
class V32Job:
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


def plan_v32_jobs(store: TradingStore) -> list[V32Job]:
    quality = build_data_quality_matrix(store)
    allowed = set(_test_pairs(quality))
    raw: list[V32Job] = []
    idx = 0
    for spec in FAMILY_SPECS:
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
    raw.sort(key=lambda j: j.key)
    if len(raw) > MAX_JOBS:
        raw = raw[:MAX_JOBS]
    out: list[V32Job] = []
    for i, j in enumerate(raw):
        out.append(
            V32Job(
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
