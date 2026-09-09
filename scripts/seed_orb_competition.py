#!/usr/bin/env python3
"""Seed ORB paper competition portfolios — INACTIVE until owner enables."""

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
from quantara_engine.competition.orb_constants import (  # noqa: E402
    ORB_COMPETITION_DESCRIPTION,
    ORB_COMPETITION_EXPERIMENT_ID,
    ORB_COMPETITION_INITIAL_CAPITAL,
    ORB_COMPETITION_NAME_HE,
    ORB_COMPETITION_PORTFOLIOS,
    ORB_COMPETITION_SUBTITLE_HE,
    ORB_SETTINGS_ENABLED_KEY,
    ORB_SETTINGS_EXPERIMENT_KEY,
    ORB_STRATEGY_SLUG,
    ORB_STRATEGY_VERSION,
)
from quantara_engine.db.session import engine  # noqa: E402


def _json(value: object) -> str:
    return json.dumps(value)


def seed_orb_competition(*, activate: bool = False) -> None:
    started_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        strategy_version = conn.execute(
            text(
                """
                SELECT sv.id FROM strategy_versions sv
                JOIN strategies s ON s.id = sv.strategy_id
                WHERE s.slug = :slug AND sv.version = :version
                """
            ),
            {"slug": ORB_STRATEGY_SLUG, "version": ORB_STRATEGY_VERSION},
        ).first()
        if not strategy_version:
            print("opening-range-breakout v1.0.0 missing — run scripts/seed_orb_strategy.py first")
            sys.exit(1)
        strategy_version_id = strategy_version[0]

        spy = conn.execute(text("SELECT id FROM instruments WHERE symbol = 'SPY'")).first()
        if not spy:
            print("SPY instrument missing — run scripts/seed_8_assets.py first")
            sys.exit(1)

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
                  start_date = EXCLUDED.start_date,
                  end_date = NULL
                """
            ),
            {
                "id": uuid.UUID(ORB_COMPETITION_EXPERIMENT_ID),
                "name": ORB_COMPETITION_NAME_HE,
                "description": f"{ORB_COMPETITION_SUBTITLE_HE}. {ORB_COMPETITION_DESCRIPTION}",
                "instrument_id": spy[0],
                "status": "draft" if not activate else "running",
                "start_date": started_at,
            },
        )

        for entry in ORB_COMPETITION_PORTFOLIOS:
            inst = conn.execute(
                text("SELECT id FROM instruments WHERE symbol = :sym"),
                {"sym": entry.symbol},
            ).first()
            if not inst:
                print(f"Instrument {entry.symbol} missing")
                sys.exit(1)
            instrument_id = inst[0]

            risk_row = conn.execute(
                text("SELECT id FROM risk_profiles WHERE slug = :slug"),
                {"slug": entry.risk_slug},
            ).first()
            if not risk_row:
                sys.exit(1)
            risk_profile_id = risk_row[0]

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
                    "initial_capital": ORB_COMPETITION_INITIAL_CAPITAL,
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
                    "strategy_version_id": strategy_version_id,
                    "instrument_id": instrument_id,
                    "timeframe": entry.timeframe,
                    "risk_profile_id": risk_profile_id,
                    "is_active": activate,
                    "experiment_id": uuid.UUID(ORB_COMPETITION_EXPERIMENT_ID),
                },
            )
            print(f"  ORB portfolio {entry.symbol} {entry.risk_slug} active={activate}")

        for key, value, description in [
            (ORB_SETTINGS_EXPERIMENT_KEY, ORB_COMPETITION_EXPERIMENT_ID, "ORB experiment UUID"),
            (ORB_SETTINGS_ENABLED_KEY, activate, "ORB paper competition enabled"),
        ]:
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
                    "key": key,
                    "value": _json(value),
                    "description": description,
                },
            )

    print("ORB competition seed completed.")
    print(f"  Experiment: {ORB_COMPETITION_EXPERIMENT_ID}")
    print(f"  Portfolios: {len(ORB_COMPETITION_PORTFOLIOS)}")
    print(f"  Paper activation: {'ENABLED' if activate else 'NOT ACTIVATED (is_active=false)'}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true", help="Enable ORB paper trading")
    args = parser.parse_args()
    seed_orb_competition(activate=args.activate)
