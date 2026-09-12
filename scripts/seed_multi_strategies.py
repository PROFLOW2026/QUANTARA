#!/usr/bin/env python3
"""Seed Mean Reversion, Volatility Squeeze, and Momentum Continuation strategy definitions."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.strategies.mean_reversion.v1_0_0 import MeanReversionV1  # noqa: E402
from quantara_engine.strategies.momentum_continuation.v1_0_0 import MomentumContinuationV1  # noqa: E402
from quantara_engine.strategies.volatility_squeeze.v1_0_0 import VolatilitySqueezeV1  # noqa: E402

STRATEGY_SPECS = [
    (uuid.UUID("00000000-0000-0000-0000-000000000510"), uuid.UUID("00000000-0000-0000-0000-000000000511"), MeanReversionV1),
    (uuid.UUID("00000000-0000-0000-0000-000000000610"), uuid.UUID("00000000-0000-0000-0000-000000000611"), VolatilitySqueezeV1),
    (uuid.UUID("00000000-0000-0000-0000-000000000710"), uuid.UUID("00000000-0000-0000-0000-000000000711"), MomentumContinuationV1),
]


def _json(value: object) -> str:
    return json.dumps(value)


def seed_multi_strategies() -> None:
    with engine.begin() as conn:
        for strategy_id, version_id, cls in STRATEGY_SPECS:
            symbols = cls.supported_instruments()
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

            timeframes = cls.supported_timeframes()
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
                      ARRAY(SELECT unnest(CAST(:timeframes AS text[]))::timeframe),
                      :status
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
                    "id": strategy_id,
                    "slug": cls.strategy_id(),
                    "name": cls.name(),
                    "description": cls.description(),
                    "symbols": symbols,
                    "timeframes": timeframes,
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
                    "id": version_id,
                    "strategy_id": strategy_id,
                    "version": cls.version(),
                    "parameters": _json(cls.default_parameters()),
                    "parameters_schema": _json(cls.parameters_schema()),
                    "logic_hash": "0" * 64,
                    "changelog": f"Initial release — {cls.name()} v{cls.version()}",
                },
            )
            print(f"  Seeded {cls.strategy_id()} v{cls.version()}")

    print("Multi-strategy definitions seed completed.")


if __name__ == "__main__":
    seed_multi_strategies()
