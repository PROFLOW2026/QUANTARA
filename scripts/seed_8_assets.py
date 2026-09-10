#!/usr/bin/env python3
"""Seed 8 target instruments for multi-asset QUANTARA."""

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
from quantara_engine.market_data.registry import list_target_assets  # noqa: E402


def seed_8_assets() -> None:
    with engine.begin() as conn:
        for asset in list_target_assets():
            existing = conn.execute(
                text("SELECT id FROM instruments WHERE symbol = :symbol"),
                {"symbol": asset.db_symbol},
            ).first()
            if existing:
                print(f"  exists: {asset.db_symbol}")
                continue

            conn.execute(
                text(
                    """
                    INSERT INTO instruments (
                      id, symbol, name, asset_class, base_currency, quote_currency,
                      pip_size, contract_size, price_tick_size, quantity_step, min_quantity,
                      trading_sessions, is_active, metadata
                    ) VALUES (
                      :id, :symbol, :name, :asset_class, :base, :quote,
                      :pip_size, 1, :tick, :step, :min_qty,
                      CAST(:sessions AS jsonb), true, CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "symbol": asset.db_symbol,
                    "name": asset.display_symbol,
                    "asset_class": asset.asset_class.value,
                    "base": asset.display_symbol.split("/")[0] if "/" in asset.display_symbol else asset.db_symbol,
                    "quote": "USD",
                    "pip_size": Decimal(asset.pip_size),
                    "tick": Decimal(asset.price_tick_size),
                    "step": Decimal(asset.quantity_step),
                    "min_qty": Decimal(asset.min_quantity),
                    "sessions": json.dumps(asset.trading_sessions),
                    "metadata": json.dumps(
                        {
                            "display_symbol": asset.display_symbol,
                            "primary_provider": asset.primary_provider.value,
                            "secondary_provider": (
                                asset.secondary_provider.value if asset.secondary_provider else None
                            ),
                        }
                    ),
                },
            )
            print(f"  inserted: {asset.db_symbol}")

        active_symbols = [asset.db_symbol for asset in list_target_assets()]
        conn.execute(
            text(
                """
                UPDATE strategies
                SET supported_instruments = (
                  SELECT array_agg(id) FROM instruments
                  WHERE symbol = ANY(:active_symbols)
                )
                WHERE slug = 'gold-trend-pullback'
                """
            ),
            {"active_symbols": active_symbols},
        )

        conn.execute(
            text(
                """
                UPDATE risk_profiles
                SET max_open_positions = 8
                WHERE slug IN (
                  'very_conservative','conservative','balanced','aggressive','very_aggressive'
                )
                """
            )
        )

    print("8-asset seed completed.")


if __name__ == "__main__":
    seed_8_assets()
