"""Persist and query market regime snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from quantara_engine.market_regime.classifier import RegimeSnapshot, classify_regime


def save_regime_snapshot(
    store,
    *,
    instrument_id: str,
    asset: str,
    timeframe: str,
    candle_time: datetime,
    snapshot: RegimeSnapshot,
) -> None:
    store.session.execute(
        text(
            """
            INSERT INTO market_regime_snapshots (
              id, instrument_id, asset, timeframe, candle_time,
              structure_regime, volatility_regime, metrics
            ) VALUES (
              :id, :instrument_id, :asset, :timeframe, :candle_time,
              :structure_regime, :volatility_regime, CAST(:metrics AS jsonb)
            )
            ON CONFLICT (asset, timeframe, candle_time) DO UPDATE SET
              structure_regime = EXCLUDED.structure_regime,
              volatility_regime = EXCLUDED.volatility_regime,
              metrics = EXCLUDED.metrics,
              instrument_id = EXCLUDED.instrument_id
            """
        ),
        {
            "id": uuid.uuid4(),
            "instrument_id": uuid.UUID(instrument_id),
            "asset": asset.upper().replace("/", ""),
            "timeframe": timeframe,
            "candle_time": candle_time,
            "structure_regime": snapshot.structure_regime.value,
            "volatility_regime": snapshot.volatility_regime.value,
            "metrics": json.dumps(snapshot.metrics),
        },
    )


def classify_and_save(store, instrument, timeframe: str, candles: list) -> RegimeSnapshot | None:
    if not candles:
        return None
    snapshot = classify_regime(candles)
    save_regime_snapshot(
        store,
        instrument_id=instrument.id,
        asset=instrument.symbol,
        timeframe=timeframe,
        candle_time=candles[-1].timestamp,
        snapshot=snapshot,
    )
    return snapshot


def latest_regimes_for_assets(store, assets: list[str], timeframe: str = "15m") -> dict[str, dict[str, Any]]:
    if not assets:
        return {}
    normalized = [a.upper().replace("/", "") for a in assets]
    rows = store.session.execute(
        text(
            """
            SELECT DISTINCT ON (asset)
              asset, structure_regime, volatility_regime, candle_time, metrics
            FROM market_regime_snapshots
            WHERE asset = ANY(:assets) AND timeframe = :timeframe
            ORDER BY asset, candle_time DESC
            """
        ),
        {"assets": normalized, "timeframe": timeframe},
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for asset, structure, volatility, candle_time, metrics in rows:
        out[asset] = {
            "structure_regime": structure,
            "volatility_regime": volatility,
            "candle_time": candle_time.isoformat() if candle_time else None,
            "metrics": metrics if isinstance(metrics, dict) else json.loads(metrics or "{}"),
        }
    return out


def regime_at_entry(store, asset: str, timeframe: str, entry_time: datetime) -> dict[str, Any] | None:
    normalized = asset.upper().replace("/", "")
    row = store.session.execute(
        text(
            """
            SELECT structure_regime, volatility_regime, candle_time, metrics
            FROM market_regime_snapshots
            WHERE asset = :asset AND timeframe = :timeframe AND candle_time <= :entry_time
            ORDER BY candle_time DESC
            LIMIT 1
            """
        ),
        {"asset": normalized, "timeframe": timeframe, "entry_time": entry_time},
    ).first()
    if not row:
        return None
    structure, volatility, candle_time, metrics = row
    return {
        "structure_regime": structure,
        "volatility_regime": volatility,
        "candle_time": candle_time.isoformat() if candle_time else None,
        "metrics": metrics if isinstance(metrics, dict) else json.loads(metrics or "{}"),
    }
