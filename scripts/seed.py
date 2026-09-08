#!/usr/bin/env python3
"""Seed Phase 1 reference data: instrument, risk profiles, strategy, settings."""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy import text  # noqa: E402

from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.core.config import settings  # noqa: E402

OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

GOLD_TREND_PULLBACK_PARAMETERS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "ema_trend": 200,
    "rsi_period": 14,
    "rsi_entry_min": 40,
    "rsi_entry_max": 60,
    "atr_period": 14,
    "atr_sl_multiplier": 1.5,
    "atr_tp_multiplier": 3.0,
    "min_candles_required": 200,
}

GOLD_TREND_PULLBACK_PARAMETERS_SCHEMA = {
    "type": "object",
    "properties": {
        "ema_fast": {"type": "integer", "minimum": 10, "maximum": 30, "default": 20},
        "ema_slow": {"type": "integer", "minimum": 30, "maximum": 100, "default": 50},
        "ema_trend": {"type": "integer", "minimum": 100, "maximum": 300, "default": 200},
        "rsi_period": {"type": "integer", "minimum": 7, "maximum": 21, "default": 14},
        "rsi_entry_min": {"type": "integer", "minimum": 30, "maximum": 50, "default": 40},
        "rsi_entry_max": {"type": "integer", "minimum": 50, "maximum": 70, "default": 60},
        "atr_period": {"type": "integer", "minimum": 7, "maximum": 21, "default": 14},
        "atr_sl_multiplier": {"type": "number", "minimum": 1.0, "maximum": 3.0, "default": 1.5},
        "atr_tp_multiplier": {"type": "number", "minimum": 2.0, "maximum": 6.0, "default": 3.0},
        "min_candles_required": {"type": "integer", "minimum": 150, "maximum": 300, "default": 200},
    },
    "required": [
        "ema_fast",
        "ema_slow",
        "ema_trend",
        "rsi_period",
        "rsi_entry_min",
        "rsi_entry_max",
        "atr_period",
        "atr_sl_multiplier",
        "atr_tp_multiplier",
        "min_candles_required",
    ],
    "additionalProperties": False,
}

RISK_PROFILES = [
    {
        "slug": "very_conservative",
        "name": "Very Conservative",
        "risk_per_trade_pct": Decimal("0.25"),
        "max_open_positions": 8,
        "max_total_exposure_pct": Decimal("100"),
        "daily_loss_limit_pct": Decimal("1.0"),
        "max_drawdown_pct": Decimal("5"),
        "is_default": False,
    },
    {
        "slug": "conservative",
        "name": "Conservative",
        "risk_per_trade_pct": Decimal("0.5"),
        "max_open_positions": 8,
        "max_total_exposure_pct": Decimal("50"),
        "daily_loss_limit_pct": Decimal("1.5"),
        "max_drawdown_pct": Decimal("5"),
        "is_default": True,
    },
    {
        "slug": "balanced",
        "name": "Balanced",
        "risk_per_trade_pct": Decimal("1.0"),
        "max_open_positions": 8,
        "max_total_exposure_pct": Decimal("100"),
        "daily_loss_limit_pct": Decimal("3.0"),
        "max_drawdown_pct": Decimal("10"),
        "is_default": False,
    },
    {
        "slug": "aggressive",
        "name": "Aggressive",
        "risk_per_trade_pct": Decimal("1.5"),
        "max_open_positions": 8,
        "max_total_exposure_pct": Decimal("100"),
        "daily_loss_limit_pct": Decimal("5.0"),
        "max_drawdown_pct": Decimal("15"),
        "is_default": False,
    },
    {
        "slug": "very_aggressive",
        "name": "Very Aggressive",
        "risk_per_trade_pct": Decimal("2.0"),
        "max_open_positions": 8,
        "max_total_exposure_pct": Decimal("100"),
        "daily_loss_limit_pct": Decimal("5.0"),
        "max_drawdown_pct": Decimal("15"),
        "is_default": False,
    },
]

SETTINGS = [
    {
        "key": "display_timezone",
        "value": settings.default_timezone,
        "description": "UI display timezone",
    },
    {
        "key": "default_risk_profile",
        "value": "balanced",
        "description": "Default risk profile slug for new strategy instances",
    },
    {
        "key": "paper_trading_enabled",
        "value": True,
        "description": "Whether paper trading mode is enabled",
    },
    {
        "key": "default_initial_capital",
        "value": int(settings.default_initial_capital),
        "description": "Default initial capital for new paper portfolios",
    },
]


def _json(value: object) -> str:
    return json.dumps(value)


def seed() -> None:
    instrument_id = uuid.uuid4()
    strategy_id = uuid.uuid4()
    strategy_version_id = uuid.uuid4()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM instruments WHERE symbol = :symbol"),
            {"symbol": "XAUUSD"},
        ).first()

        if existing:
            instrument_id = existing[0]
            sv = conn.execute(
                text(
                    """
                    SELECT sv.id FROM strategy_versions sv
                    JOIN strategies s ON s.id = sv.strategy_id
                    WHERE s.slug = 'gold-trend-pullback' AND sv.version = '1.0.0'
                    """
                ),
            ).first()
            if sv:
                strategy_version_id = sv[0]
            print("Seed reference data already present (XAUUSD exists).")
            return

        conn.execute(
            text(
                """
                INSERT INTO instruments (
                  id, symbol, name, asset_class, base_currency, quote_currency,
                  pip_size, contract_size, price_tick_size, quantity_step, min_quantity,
                  trading_sessions, is_active, metadata
                ) VALUES (
                  :id, :symbol, :name, :asset_class, :base_currency, :quote_currency,
                  :pip_size, :contract_size, :price_tick_size, :quantity_step, :min_quantity,
                  :trading_sessions, :is_active, :metadata
                )
                """
            ),
            {
                "id": instrument_id,
                "symbol": "XAUUSD",
                "name": "Gold / US Dollar",
                "asset_class": "commodity",
                "base_currency": "XAU",
                "quote_currency": "USD",
                "pip_size": Decimal("0.01"),
                "contract_size": Decimal("100"),
                "price_tick_size": Decimal("0.01"),
                "quantity_step": Decimal("0.01"),
                "min_quantity": Decimal("0.01"),
                "trading_sessions": _json({"sessions": ["24x5"]}),
                "is_active": True,
                "metadata": _json({"description": "Spot gold vs USD"}),
            },
        )

        for profile in RISK_PROFILES:
            conn.execute(
                text(
                    """
                    INSERT INTO risk_profiles (
                      id, name, slug, risk_per_trade_pct, max_open_positions,
                      max_total_exposure_pct, daily_loss_limit_pct, max_drawdown_pct,
                      is_default, parameters
                    ) VALUES (
                      :id, :name, :slug, :risk_per_trade_pct, :max_open_positions,
                      :max_total_exposure_pct, :daily_loss_limit_pct, :max_drawdown_pct,
                      :is_default, :parameters
                    )
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    **profile,
                    "parameters": _json({"volatility_check_enabled": False}),
                },
            )

        conn.execute(
            text(
                """
                INSERT INTO strategies (
                  id, slug, name, description, supported_instruments,
                  supported_timeframes, status
                ) VALUES (
                  :id, :slug, :name, :description, :supported_instruments,
                  ARRAY['1h']::timeframe[], :status
                )
                """
            ),
            {
                "id": strategy_id,
                "slug": "gold-trend-pullback",
                "name": "Gold Trend Pullback",
                "description": "Trend-following pullback strategy for XAU/USD on 1h candles.",
                "supported_instruments": [instrument_id],
                "status": "active",
            },
        )

        conn.execute(
            text(
                """
                INSERT INTO strategy_versions (
                  id, strategy_id, version, version_major, version_minor, version_patch,
                  parameters, parameters_schema, risk_profile_compatibility,
                  logic_hash, changelog, is_active
                ) VALUES (
                  :id, :strategy_id, :version, :version_major, :version_minor, :version_patch,
                  :parameters, :parameters_schema,
                  ARRAY['very_conservative', 'conservative', 'balanced', 'aggressive', 'very_aggressive']::risk_profile_slug[],
                  :logic_hash, :changelog, :is_active
                )
                """
            ),
            {
                "id": strategy_version_id,
                "strategy_id": strategy_id,
                "version": "1.0.0",
                "version_major": 1,
                "version_minor": 0,
                "version_patch": 0,
                "parameters": _json(GOLD_TREND_PULLBACK_PARAMETERS),
                "parameters_schema": _json(GOLD_TREND_PULLBACK_PARAMETERS_SCHEMA),
                "logic_hash": "0" * 64,
                "changelog": "Initial release — gold trend pullback v1.0.0",
                "is_active": True,
            },
        )

        for setting in SETTINGS:
            conn.execute(
                text(
                    """
                    INSERT INTO settings (id, key, value, description)
                    VALUES (:id, :key, :value, :description)
                    ON CONFLICT (key) DO UPDATE SET
                      value = EXCLUDED.value,
                      description = EXCLUDED.description,
                      updated_at = NOW()
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "key": setting["key"],
                    "value": _json(setting["value"]),
                    "description": setting["description"],
                },
            )

    print("Seed completed successfully.")
    print(f"  Instrument: XAUUSD ({instrument_id})")
    print(f"  Strategy: gold-trend-pullback v1.0.0 ({strategy_version_id})")
    print(f"  Risk profiles: conservative (50%), balanced (100%), aggressive (100%)")
    print(f"  Settings: {len(SETTINGS)} keys")


if __name__ == "__main__":
    seed()
