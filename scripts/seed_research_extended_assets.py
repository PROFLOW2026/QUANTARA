#!/usr/bin/env python3
"""Seed research-extended instruments (does not change Live Sim 8-asset core)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("DATABASE_URL", "").strip():
    pass
else:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

ENGINE_PATH = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy import text  # noqa: E402

from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.market_data.registry import RESEARCH_EXTENDED_ASSETS  # noqa: E402


def seed_research_extended_assets() -> None:
    with engine.begin() as conn:
        for asset in RESEARCH_EXTENDED_ASSETS:
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
                    "base": asset.display_symbol.split("/")[0]
                    if "/" in asset.display_symbol
                    else asset.db_symbol,
                    "quote": asset.display_symbol.split("/")[1]
                    if "/" in asset.display_symbol
                    else "USD",
                    "pip_size": Decimal(asset.pip_size),
                    "tick": Decimal(asset.price_tick_size),
                    "step": Decimal(asset.quantity_step),
                    "min_qty": Decimal(asset.min_quantity),
                    "sessions": json.dumps(asset.trading_sessions),
                    "metadata": json.dumps(
                        {
                            "display_symbol": asset.display_symbol,
                            "primary_provider": asset.primary_provider.value,
                            "research_extended": True,
                        }
                    ),
                },
            )
            print(f"  inserted: {asset.db_symbol}")


if __name__ == "__main__":
    seed_research_extended_assets()
