#!/usr/bin/env python3
"""Sync V3.2 Stage-B qualified candidates to Live Sim strategy instances (no account reset)."""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.competition.constants import OWNER_ID
from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.v32_registry import (
    V32_LIVE_SIM_EXPERIMENT_ID,
    load_v32_qualified_combinations,
    parameter_overrides_for_combination,
    v32_instance_id_for_key,
    v32_portfolio_id_for_key,
)

V32_STRATEGY_SLUG = "v32-p2-live-sim"
V32_STRATEGY_VERSION = "1.0.0"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _ensure_experiment(conn) -> None:
    inst = conn.execute(
        text("SELECT id FROM instruments WHERE symbol = 'AMD' LIMIT 1")
    ).first()
    if not inst:
        raise SystemExit("AMD instrument missing — run seed_8_assets.py")
    conn.execute(
        text(
            """
            INSERT INTO experiments (id, name, description, instrument_id, status, start_date)
            VALUES (
              CAST(:id AS uuid),
              'V3.2 Live Sim',
              'Stage-B qualified rule families — virtual Live Sim only',
              :iid,
              'running',
              NOW()
            )
            ON CONFLICT (id) DO UPDATE SET status = 'running', end_date = NULL
            """
        ),
        {"id": V32_LIVE_SIM_EXPERIMENT_ID, "iid": inst[0]},
    )


def _ensure_portfolio(conn, *, key: str) -> str:
    pid = v32_portfolio_id_for_key(key)
    conn.execute(
        text(
            """
            INSERT INTO portfolios (
              id, owner_id, name, mode, initial_capital, balance, unrealized_pnl,
              equity, exposure_notional, reserved_capital, currency, status, peak_equity
            ) VALUES (
              CAST(:id AS uuid), CAST(:owner AS uuid),
              :name, 'paper', 10000, 10000, 0,
              10000, 0, 0, 'USD', 'active', 10000
            )
            ON CONFLICT (id) DO UPDATE SET status = 'active', name = EXCLUDED.name
            """
        ),
        {"id": pid, "owner": OWNER_ID, "name": f"V3.2 Live Sim — {key}"},
    )
    return pid


def _ensure_v32_strategy(conn) -> uuid.UUID:
    row = conn.execute(
        text(
            """
            SELECT sv.id FROM strategy_versions sv
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE s.slug = :slug AND sv.version = :ver
            """
        ),
        {"slug": V32_STRATEGY_SLUG, "ver": V32_STRATEGY_VERSION},
    ).first()
    if row:
        return row[0]
    sid = uuid.uuid4()
    vid = uuid.uuid4()
    inst_ids = [
        r[0]
        for r in conn.execute(
            text("SELECT id FROM instruments WHERE symbol = ANY(:syms)"),
            {"syms": ["BTCUSD", "ETHUSD", "XAUUSD", "GBPJPY", "NVDA", "TSLA", "AMD", "COIN"]},
        ).fetchall()
    ]
    conn.execute(
        text(
            """
            INSERT INTO strategies (
              id, slug, name, description, supported_instruments,
              supported_timeframes, status
            ) VALUES (
              :id, :slug, :name, :description, :inst,
              ARRAY['5m','15m','1h']::timeframe[], 'active'
            )
            ON CONFLICT (slug) DO NOTHING
            """
        ),
        {
            "id": sid,
            "slug": V32_STRATEGY_SLUG,
            "name": "V3.2 Live Sim",
            "description": "Stage-B qualified V3.2 rule families",
            "inst": inst_ids,
        },
    )
    strat_id = conn.execute(
        text("SELECT id FROM strategies WHERE slug = :slug"),
        {"slug": V32_STRATEGY_SLUG},
    ).scalar()
    conn.execute(
        text(
            """
            INSERT INTO strategy_versions (
              id, strategy_id, version, version_major, version_minor, version_patch,
              parameters, parameters_schema, risk_profile_compatibility,
              logic_hash, changelog, is_active
            ) VALUES (
              :id, :sid, :ver, 1, 0, 0,
              CAST(:params AS jsonb), CAST(:params AS jsonb),
              ARRAY['balanced','conservative','aggressive']::risk_profile_slug[],
              :hash, :changelog, TRUE
            )
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "id": vid,
            "sid": strat_id,
            "ver": V32_STRATEGY_VERSION,
            "params": json.dumps({"family": "rsi_divergence_mr", "trade_direction": "long"}),
            "hash": "v32" + "0" * 61,
            "changelog": "V3.2 Live Sim rule replay",
        },
    )
    row = conn.execute(
        text(
            """
            SELECT sv.id FROM strategy_versions sv
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE s.slug = :slug AND sv.version = :ver
            """
        ),
        {"slug": V32_STRATEGY_SLUG, "ver": V32_STRATEGY_VERSION},
    ).first()
    if not row:
        raise SystemExit(f"failed to register {V32_STRATEGY_SLUG}")
    return row[0]


def _risk_profile_id(conn) -> uuid.UUID:
    row = conn.execute(
        text("SELECT id FROM risk_profiles WHERE slug = 'balanced' LIMIT 1")
    ).first()
    if not row:
        raise SystemExit("missing risk profile balanced")
    return row[0]


def main() -> None:
    combos = load_v32_qualified_combinations()
    if not combos:
        raise SystemExit("no qualified combinations in v32_qualified_candidates.json")

    session = sessionmaker(bind=create_engine(normalize_sqlalchemy_postgres_url(db_url())))()
    conn = session.connection()
    _ensure_experiment(conn)
    sv_id = _ensure_v32_strategy(conn)
    rp_id = _risk_profile_id(conn)

    active_keys: list[str] = []
    created = 0
    updated = 0
    missing_inst: list[str] = []

    for row in combos:
        key = row["key"]
        active_keys.append(key)
        inst = conn.execute(
            text("SELECT id FROM instruments WHERE symbol = :sym"),
            {"sym": row["asset"]},
        ).first()
        if not inst:
            missing_inst.append(row["asset"])
            continue
        iid = inst[0]
        instance_id = v32_instance_id_for_key(key)
        portfolio_id = _ensure_portfolio(conn, key=key)
        overrides = parameter_overrides_for_combination(row)
        result = conn.execute(
            text(
                """
                INSERT INTO strategy_instances (
                  id, portfolio_id, strategy_version_id, instrument_id,
                  timeframe, risk_profile_id, parameter_overrides, is_active, experiment_id
                ) VALUES (
                  CAST(:id AS uuid), CAST(:pid AS uuid), :sv, :iid,
                  :tf, :rp, CAST(:params AS jsonb), TRUE, CAST(:exp AS uuid)
                )
                ON CONFLICT (id) DO UPDATE SET
                  is_active = TRUE,
                  instrument_id = EXCLUDED.instrument_id,
                  timeframe = EXCLUDED.timeframe,
                  parameter_overrides = EXCLUDED.parameter_overrides,
                  experiment_id = EXCLUDED.experiment_id
                """
            ),
            {
                "id": instance_id,
                "pid": portfolio_id,
                "sv": sv_id,
                "iid": iid,
                "tf": row["timeframe"],
                "rp": rp_id,
                "params": json.dumps(overrides),
                "exp": V32_LIVE_SIM_EXPERIMENT_ID,
            },
        )
        if result.rowcount:
            created += 1
        else:
            updated += 1

    conn.execute(
        text(
            """
            UPDATE strategy_instances si SET is_active = FALSE
            FROM strategy_versions sv
            JOIN strategies s ON s.id = sv.strategy_id
            WHERE si.strategy_version_id = sv.id
              AND s.slug = :slug
              AND si.experiment_id = CAST(:exp AS uuid)
              AND NOT (si.parameter_overrides->>'v32_candidate_key') = ANY(:keys)
            """
        ),
        {"slug": V32_STRATEGY_SLUG, "exp": V32_LIVE_SIM_EXPERIMENT_ID, "keys": active_keys},
    )

    session.commit()
    session.close()
    print(
        json.dumps(
            {
                "ok": True,
                "qualified": len(combos),
                "instances_upserted": created + updated,
                "missing_instruments": sorted(set(missing_inst)),
                "keys": active_keys,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
