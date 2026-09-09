#!/usr/bin/env python3
"""Seed Opening Range Breakout strategy definition (Robot B)."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.strategies.opening_range_breakout.v1_0_0 import OpeningRangeBreakoutV1  # noqa: E402

STRATEGY_ID = uuid.UUID("00000000-0000-0000-0000-000000000310")
VERSION_ID = uuid.UUID("00000000-0000-0000-0000-000000000311")


def _json(value: object) -> str:
    return json.dumps(value)


def seed_orb_strategy() -> None:
    cls = OpeningRangeBreakoutV1
    symbols = cls.supported_instruments()

    with engine.begin() as conn:
        missing = [
            sym
            for sym in symbols
            if not conn.execute(
                text("SELECT 1 FROM instruments WHERE symbol = :sym"),
                {"sym": sym},
            ).first()
        ]
        if missing:
            print(f"Instruments missing {missing} — run scripts/seed_8_assets.py first")
            sys.exit(1)

        conn.execute(
            text(
                """
                INSERT INTO strategies (
                  id, slug, name, description, supported_instruments,
                  supported_timeframes, status
                ) VALUES (
                  :id, :slug, :name, :description,
                  ARRAY(
                    SELECT id FROM instruments
                    WHERE symbol = ANY(:symbols)
                  ),
                  ARRAY['5m']::timeframe[], :status
                )
                ON CONFLICT (slug) DO UPDATE SET
                  name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  supported_instruments = EXCLUDED.supported_instruments,
                  supported_timeframes = EXCLUDED.supported_timeframes,
                  status = EXCLUDED.status
                """
            ),
            {
                "id": STRATEGY_ID,
                "slug": cls.strategy_id(),
                "name": cls.name(),
                "description": cls.description(),
                "symbols": symbols,
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
                  :id, :strategy_id, :version, 1, 0, 0,
                  :parameters, :parameters_schema,
                  ARRAY['very_conservative','conservative','balanced','aggressive','very_aggressive']::risk_profile_slug[],
                  :logic_hash, :changelog, true
                )
                ON CONFLICT (strategy_id, version) DO UPDATE SET
                  parameters = EXCLUDED.parameters,
                  parameters_schema = EXCLUDED.parameters_schema,
                  risk_profile_compatibility = EXCLUDED.risk_profile_compatibility,
                  is_active = true
                """
            ),
            {
                "id": VERSION_ID,
                "strategy_id": STRATEGY_ID,
                "version": cls.version(),
                "parameters": _json(cls.default_parameters()),
                "parameters_schema": _json(cls.parameters_schema()),
                "logic_hash": "0" * 64,
                "changelog": "Initial release — ORB v1.0.0 US equities RTH",
            },
        )

    print("ORB strategy seed completed.")
    print(f"  Strategy: {cls.strategy_id()} v{cls.version()} ({VERSION_ID})")


if __name__ == "__main__":
    seed_orb_strategy()
