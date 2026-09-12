#!/usr/bin/env python3
"""Seed Robots C/D/E paper competition portfolios — idempotent."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import OWNER_ID  # noqa: E402
from quantara_engine.competition.multi_strategy_constants import (  # noqa: E402
    ALL_MULTI_STRATEGY_PORTFOLIOS,
    EXPERIMENT_META,
    MEAN_REVERSION_EXPERIMENT_ID,
    MEAN_REVERSION_SETTINGS_ENABLED_KEY,
    MEAN_REVERSION_STRATEGY_SLUG,
    MEAN_REVERSION_STRATEGY_VERSION,
    MOMENTUM_CONTINUATION_EXPERIMENT_ID,
    MOMENTUM_CONTINUATION_SETTINGS_ENABLED_KEY,
    MOMENTUM_CONTINUATION_STRATEGY_SLUG,
    MOMENTUM_CONTINUATION_STRATEGY_VERSION,
    MULTI_STRATEGY_INITIAL_CAPITAL,
    SETTINGS_BY_STRATEGY,
    VOLATILITY_SQUEEZE_EXPERIMENT_ID,
    VOLATILITY_SQUEEZE_SETTINGS_ENABLED_KEY,
    VOLATILITY_SQUEEZE_STRATEGY_SLUG,
    VOLATILITY_SQUEEZE_STRATEGY_VERSION,
)
from quantara_engine.db.session import engine  # noqa: E402

EXPERIMENT_SPECS = [
    (MEAN_REVERSION_EXPERIMENT_ID, MEAN_REVERSION_STRATEGY_SLUG, MEAN_REVERSION_STRATEGY_VERSION, MEAN_REVERSION_SETTINGS_ENABLED_KEY),
    (VOLATILITY_SQUEEZE_EXPERIMENT_ID, VOLATILITY_SQUEEZE_STRATEGY_SLUG, VOLATILITY_SQUEEZE_STRATEGY_VERSION, VOLATILITY_SQUEEZE_SETTINGS_ENABLED_KEY),
    (MOMENTUM_CONTINUATION_EXPERIMENT_ID, MOMENTUM_CONTINUATION_STRATEGY_SLUG, MOMENTUM_CONTINUATION_STRATEGY_VERSION, MOMENTUM_CONTINUATION_SETTINGS_ENABLED_KEY),
]


def _json(value: object) -> str:
    return json.dumps(value)


def seed_multi_strategy_competition(*, activate: bool = True) -> None:
    started_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        nvda = conn.execute(text("SELECT id FROM instruments WHERE symbol = 'NVDA'")).first()
        if not nvda:
            print("NVDA instrument missing — run scripts/seed_8_assets.py first")
            sys.exit(1)
        instrument_anchor_id = nvda[0]

        version_by_slug: dict[str, object] = {}
        for _, slug, version, _ in EXPERIMENT_SPECS:
            row = conn.execute(
                text(
                    """
                    SELECT sv.id FROM strategy_versions sv
                    JOIN strategies s ON s.id = sv.strategy_id
                    WHERE s.slug = :slug AND sv.version = :version
                    """
                ),
                {"slug": slug, "version": version},
            ).first()
            if not row:
                print(f"{slug} v{version} missing — run scripts/seed_multi_strategies.py first")
                sys.exit(1)
            version_by_slug[slug] = row[0]

        for experiment_id, slug, _, settings_key in EXPERIMENT_SPECS:
            meta = EXPERIMENT_META[experiment_id]
            conn.execute(
                text(
                    """
                    INSERT INTO experiments (
                      id, name, description, instrument_id, status, start_date
                    ) VALUES (
                      :id, :name, :description, :instrument_id, :status, :start_date
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      name = EXCLUDED.name,
                      description = EXCLUDED.description,
                      status = EXCLUDED.status,
                      start_date = COALESCE(experiments.start_date, EXCLUDED.start_date),
                      end_date = NULL
                    """
                ),
                {
                    "id": uuid.UUID(experiment_id),
                    "name": meta["name_he"],
                    "description": f"{meta['subtitle_he']}. {meta['description']}",
                    "instrument_id": instrument_anchor_id,
                    "status": "running" if activate else "draft",
                    "start_date": started_at,
                },
            )
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
                    "key": settings_key,
                    "value": _json(activate),
                    "description": f"{slug} paper competition enabled",
                },
            )

        for entry in ALL_MULTI_STRATEGY_PORTFOLIOS:
            inst = conn.execute(
                text("SELECT id FROM instruments WHERE symbol = :sym"),
                {"sym": entry.symbol},
            ).first()
            if not inst:
                print(f"Instrument {entry.symbol} missing")
                sys.exit(1)

            risk_row = conn.execute(
                text("SELECT id FROM risk_profiles WHERE slug = :slug"),
                {"slug": entry.risk_slug},
            ).first()
            if not risk_row:
                sys.exit(1)

            conn.execute(
                text(
                    """
                    INSERT INTO portfolios (
                      id, owner_id, name, mode, initial_capital, balance, unrealized_pnl,
                      equity, exposure_notional, reserved_capital, currency, status, peak_equity
                    ) VALUES (
                      :id, :owner_id, :name, 'paper', :initial_capital, :initial_capital, 0,
                      :initial_capital, 0, 0, 'USD', 'active', :initial_capital
                    )
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """
                ),
                {
                    "id": uuid.UUID(entry.portfolio_id),
                    "owner_id": uuid.UUID(OWNER_ID),
                    "name": entry.name_he,
                    "initial_capital": MULTI_STRATEGY_INITIAL_CAPITAL,
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO strategy_instances (
                      id, portfolio_id, strategy_version_id, instrument_id,
                      timeframe, risk_profile_id, parameter_overrides, is_active, experiment_id
                    ) VALUES (
                      :id, :portfolio_id, :strategy_version_id, :instrument_id,
                      :timeframe, :risk_profile_id, '{}', :is_active, :experiment_id
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      instrument_id = EXCLUDED.instrument_id,
                      experiment_id = EXCLUDED.experiment_id,
                      is_active = EXCLUDED.is_active,
                      timeframe = EXCLUDED.timeframe
                    """
                ),
                {
                    "id": uuid.UUID(entry.instance_id),
                    "portfolio_id": uuid.UUID(entry.portfolio_id),
                    "strategy_version_id": version_by_slug[entry.strategy_slug],
                    "instrument_id": inst[0],
                    "timeframe": entry.timeframe,
                    "risk_profile_id": risk_row[0],
                    "is_active": activate,
                    "experiment_id": uuid.UUID(entry.experiment_id),
                },
            )

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
                "key": "multi_strategy_competition_started_at",
                "value": _json(started_at.isoformat()),
                "description": "Robots C/D/E activation timestamp for rankings context",
            },
        )

    print("Multi-strategy competition seed completed.")
    print(f"  Portfolios: {len(ALL_MULTI_STRATEGY_PORTFOLIOS)}")
    print(f"  Activation: {'ENABLED' if activate else 'NOT ACTIVATED'}")
    for slug, key in SETTINGS_BY_STRATEGY.items():
        print(f"  {slug}: {key}={activate}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true", default=True)
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    activate = not args.no_activate
    seed_multi_strategy_competition(activate=activate)
