#!/usr/bin/env python3
"""Seed the active 15-portfolio multi-timeframe competition experiment.

Preserves the legacy 5-portfolio experiment (archived, not deleted).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE_PATH))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_DESCRIPTION,
    COMPETITION_INITIAL_CAPITAL,
    COMPETITION_NAME_HE,
    COMPETITION_SUBTITLE_HE,
    LEGACY_COMPETITION_EXPERIMENT_ID,
    LEGACY_COMPETITION_PORTFOLIOS,
    OWNER_ID,
)
from quantara_engine.db.session import engine  # noqa: E402


def _json(value: object) -> str:
    return json.dumps(value)


def seed_competition() -> None:
    started_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        instrument = conn.execute(
            text("SELECT id FROM instruments WHERE symbol = 'XAUUSD'"),
        ).first()
        if not instrument:
            print("XAUUSD instrument missing — run scripts/seed.py first")
            sys.exit(1)
        instrument_id = instrument[0]

        strategy_version = conn.execute(
            text(
                """
                SELECT sv.id FROM strategy_versions sv
                JOIN strategies s ON s.id = sv.strategy_id
                WHERE s.slug = 'gold-trend-pullback' AND sv.version = '1.0.0'
                """
            ),
        ).first()
        if not strategy_version:
            print("gold-trend-pullback v1.0.0 missing — run scripts/seed.py first")
            sys.exit(1)
        strategy_version_id = strategy_version[0]

        # Archive legacy 5-portfolio experiment (preserve rows).
        conn.execute(
            text(
                """
                UPDATE experiments
                SET status = 'completed', end_date = COALESCE(end_date, NOW())
                WHERE id = :id
                """
            ),
            {"id": uuid.UUID(LEGACY_COMPETITION_EXPERIMENT_ID)},
        )

        for entry in LEGACY_COMPETITION_PORTFOLIOS:
            conn.execute(
                text(
                    """
                    UPDATE strategy_instances
                    SET is_active = false
                    WHERE id = :id
                    """
                ),
                {"id": uuid.UUID(entry.instance_id)},
            )

        # Active 15-portfolio experiment — always a fresh start timestamp.
        conn.execute(
            text(
                """
                INSERT INTO experiments (
                  id, name, description, instrument_id, status, start_date
                ) VALUES (
                  :id, :name, :description, :instrument_id, 'running', :start_date
                )
                ON CONFLICT (id) DO UPDATE SET
                  name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  status = 'running',
                  start_date = EXCLUDED.start_date,
                  end_date = NULL
                """
            ),
            {
                "id": uuid.UUID(ACTIVE_COMPETITION_EXPERIMENT_ID),
                "name": COMPETITION_NAME_HE,
                "description": f"{COMPETITION_SUBTITLE_HE}. {COMPETITION_DESCRIPTION}",
                "instrument_id": instrument_id,
                "start_date": started_at,
            },
        )

        for entry in ACTIVE_COMPETITION_PORTFOLIOS:
            risk_row = conn.execute(
                text("SELECT id FROM risk_profiles WHERE slug = :slug"),
                {"slug": entry.risk_slug},
            ).first()
            if not risk_row:
                print(f"Risk profile {entry.risk_slug} missing — run migration 0002 first")
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
                    ON CONFLICT (id) DO UPDATE SET
                      name = EXCLUDED.name,
                      status = 'active'
                    """
                ),
                {
                    "id": uuid.UUID(entry.portfolio_id),
                    "owner_id": uuid.UUID(OWNER_ID),
                    "name": entry.name_he,
                    "initial_capital": COMPETITION_INITIAL_CAPITAL,
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
                      :timeframe, :risk_profile_id, '{}', true, :experiment_id
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      is_active = true,
                      experiment_id = EXCLUDED.experiment_id,
                      risk_profile_id = EXCLUDED.risk_profile_id,
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
                    "experiment_id": uuid.UUID(ACTIVE_COMPETITION_EXPERIMENT_ID),
                },
            )
            print(
                f"  Portfolio {entry.portfolio_id} tf={entry.timeframe} risk={entry.risk_slug}"
            )

        for key, value, description in [
            (
                "competition_experiment_id",
                ACTIVE_COMPETITION_EXPERIMENT_ID,
                "Active multi-timeframe competition experiment UUID",
            ),
            (
                "competition_started_at",
                started_at.isoformat(),
                "Active competition experiment start timestamp (UTC ISO)",
            ),
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

    print("Competition seed completed.")
    print(f"  Legacy experiment archived: {LEGACY_COMPETITION_EXPERIMENT_ID}")
    print(f"  Active experiment: {ACTIVE_COMPETITION_EXPERIMENT_ID}")
    print(f"  Common start: {started_at.isoformat()}")
    print(
        f"  Portfolios: {len(ACTIVE_COMPETITION_PORTFOLIOS)} × ${COMPETITION_INITIAL_CAPITAL}"
    )


if __name__ == "__main__":
    seed_competition()
