"""Rollout script logic — trade scope, portfolio counts, dry-run safety, write path."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from quantara_engine.competition import realistic_broker_rollout as rollout
from quantara_engine.competition.paper_run import (
    CURRENT_PAPER_RUN_SETTING,
    count_scoped_closed_trades,
    create_paper_run,
    get_current_paper_run_id,
)
from quantara_engine.competition.realistic_broker_rollout import (
    RolloutError,
    active_research_portfolio_counts,
    build_dry_run_report,
    canonical_research_portfolio_counts,
    capture_environment_snapshot,
    execute_rollout,
    live_sim_in_place_upgrade_eligible,
    snapshots_equal,
    validate_rollout_preconditions,
)
from quantara_engine.db.session import SessionLocal
from quantara_engine.persistence.store import TradingStore
from tests.broker_integration_support import provision_broker_test_database


def test_canonical_research_portfolio_counts():
    counts = canonical_research_portfolio_counts()
    assert counts.total == 280
    assert counts.robot_a == 120
    assert counts.robot_b == 40
    assert counts.robot_c == 40
    assert counts.robot_d == 40
    assert counts.robot_e == 40


def test_build_dry_run_report_mocked_counts():
    store = MagicMock()
    counts = canonical_research_portfolio_counts()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rollout, "get_current_paper_run_id", lambda _s: rollout.EXPECTED_LEGACY_RUN_ID)
        mp.setattr(rollout, "count_scoped_closed_trades", lambda _s, _r: 64)
        mp.setattr(rollout, "active_research_portfolio_counts", lambda _s: counts)
        mp.setattr(rollout, "_all_broker_orders_count", lambda _s: 181)
        mp.setattr(rollout, "_all_broker_fills_count", lambda _s: 108)
        mp.setattr(rollout, "_broker_fill_count", lambda _s, _slug: 0)
        mp.setattr(rollout, "_open_position_count", lambda _s, _slug: 0)
        mp.setattr(rollout, "_rejected_order_count", lambda _s, _slug: 25 if _slug == "live-sim-10k" else 0)
        mp.setattr(
            rollout,
            "_account_row",
            lambda _s, slug: {
                "cash": "10000",
                "balance": "10000",
                "equity": "10000",
                "realized_pnl": "0",
                "spot_crypto_cash": "10000",
                "execution_model": "legacy_spot_limited",
            },
        )
        mp.setattr(rollout, "live_sim_in_place_upgrade_eligible", lambda _s: True)
        report = build_dry_run_report(store)

    assert report["historical_closed_trades_preserved"] == 64
    assert report["historical_orders_preserved"] == 181
    assert report["historical_fills_preserved"] == 108
    assert report["research_portfolios"] == 280
    assert report["robot_A_portfolios"] == 120
    assert report["robot_B_portfolios"] == 40
    assert report["robot_C_portfolios"] == 40
    assert report["robot_D_portfolios"] == 40
    assert report["robot_E_portfolios"] == 40
    assert report["historical_short_denials_replayed"] == 0
    assert report["actual_db_changes"] == 0


@pytest.fixture(scope="module")
def rollout_db():
    url = provision_broker_test_database()
    return url


def _seed_multi_strategy_if_needed(store: TradingStore) -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    env = __import__("os").environ.copy()
    env["DATABASE_URL"] = store.session.bind.url.render_as_string(hide_password=False)
    for name in ("seed_multi_strategies.py", "seed_multi_strategy_competition.py"):
        script = root / "scripts" / name
        if script.exists():
            subprocess.run([sys.executable, str(script)], cwd=str(root), check=True, env=env)


def _ensure_research_account(store: TradingStore) -> None:
    row = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = 'quantara_paper_competition'")
    ).scalar()
    if row:
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET execution_model = 'legacy_spot_limited',
                    cash = 320000, balance = 320000, equity = 320000,
                    realized_pnl = 0, spot_crypto_cash = 320000,
                    is_active = TRUE, account_state = 'active'
                WHERE slug = 'quantara_paper_competition'
                """
            )
        )
        return
    aid = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              id, slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
              realized_pnl, gross_realized_pnl, fees_paid, spot_crypto_cash,
              is_active, pending_owner_reset, account_state, execution_model
            ) VALUES (
              :id, 'quantara_paper_competition', 'quantara_standard_paper', 'netting',
              320000, 320000, 320000, 320000, 0, 0, 0, 320000,
              TRUE, FALSE, 'active', 'legacy_spot_limited'
            )
            """
        ),
        {"id": aid},
    )


def _ensure_live_sim_account(store: TradingStore) -> None:
    row = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = 'live-sim-10k'")
    ).scalar()
    if row:
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET cash = 10000, balance = 10000, equity = 10000, realized_pnl = 0,
                    spot_crypto_cash = 10000, execution_model = 'legacy_spot_limited'
                WHERE slug = 'live-sim-10k'
                """
            )
        )
        return
    store.session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              id, slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
              realized_pnl, gross_realized_pnl, fees_paid, spot_crypto_cash,
              is_active, pending_owner_reset, account_state, execution_model
            ) VALUES (
              :id, 'live-sim-10k', 'quantara_standard_paper', 'netting',
              10000, 10000, 10000, 10000, 0, 0, 0, 10000,
              TRUE, FALSE, 'active', 'legacy_spot_limited'
            )
            """
        ),
        {"id": str(uuid.uuid4())},
    )


def _enable_full_competition(store: TradingStore) -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    env = __import__("os").environ.copy()
    env["DATABASE_URL"] = store.session.bind.url.render_as_string(hide_password=False)
    subprocess.run(
        [sys.executable, str(root / "scripts" / "seed_orb_competition.py"), "--activate"],
        cwd=str(root),
        check=True,
        env=env,
    )
    store.update_settings("orb_competition_enabled", True)
    store._competition_entries_cache = None


def _insert_legacy_trade_with_position_lineage(store: TradingStore, run_id: str) -> None:
    row = store.session.execute(
        text(
            """
            SELECT p.id::text AS portfolio_id, si.id::text AS instance_id,
                   si.instrument_id::text AS inst_id, si.strategy_version_id::text AS sv_id
            FROM portfolios p
            JOIN strategy_instances si ON si.portfolio_id = p.id
            WHERE p.mode = 'paper'
            LIMIT 1
            """
        )
    ).mappings().first()
    assert row
    pos_id = str(uuid.uuid4())
    trade_id = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO positions (
              id, portfolio_id, strategy_instance_id, instrument_id, direction, quantity,
              entry_price, current_price, stop_loss, unrealized_pnl, status, opened_at,
              mode, paper_run_id
            ) VALUES (
              :pid, :port, :inst_id, :inst, 'long', 1, 100, 101, 95, 0, 'closed', NOW(),
              'paper', CAST(:run AS uuid)
            )
            """
        ),
        {
            "pid": pos_id,
            "port": row["portfolio_id"],
            "inst_id": row["instance_id"],
            "inst": row["inst_id"],
            "run": run_id,
        },
    )
    store.session.execute(
        text(
            """
            INSERT INTO trades (
              id, portfolio_id, position_id, strategy_instance_id, strategy_version_id,
              instrument_id, direction, quantity, entry_price, exit_price,
              gross_pnl, realized_pnl, fees_total, slippage_total, spread_total,
              target_risk_amount, actual_risk_amount, exit_reason, duration_seconds,
              opened_at, closed_at, mode, paper_run_id
            ) VALUES (
              :tid, :port, :pid, :inst_id, :sv_id, :inst, 'long', 1,
              100, 101, 1, 1, 0, 0, 0, 10, 10, 'tp', 60,
              NOW(), NOW(), 'paper', NULL
            )
            """
        ),
        {
            "tid": trade_id,
            "port": row["portfolio_id"],
            "pid": pos_id,
            "inst_id": row["instance_id"],
            "sv_id": row["sv_id"],
            "inst": row["inst_id"],
        },
    )


@pytest.mark.integration
def test_count_scoped_closed_trades_uses_position_lineage(rollout_db, monkeypatch):
    session = SessionLocal()
    store = TradingStore(session)
    legacy_run = str(uuid.uuid4())
    monkeypatch.setattr(rollout, "EXPECTED_LEGACY_RUN_ID", legacy_run)
    try:
        create_paper_run(store, starting_broker_cash=Decimal("320000"), run_id=legacy_run)
        _insert_legacy_trade_with_position_lineage(store, legacy_run)
        session.flush()
        assert count_scoped_closed_trades(store, legacy_run) == 1
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_dry_run_has_zero_side_effects(rollout_db, monkeypatch):
    session = SessionLocal()
    store = TradingStore(session)
    legacy_run = str(uuid.uuid4())
    monkeypatch.setattr(rollout, "EXPECTED_LEGACY_RUN_ID", legacy_run)
    try:
        _seed_multi_strategy_if_needed(store)
        _enable_full_competition(store)
        _ensure_research_account(store)
        _ensure_live_sim_account(store)
        create_paper_run(
            store,
            starting_broker_cash=Decimal("320000"),
            execution_model="legacy_spot_limited",
            run_id=legacy_run,
        )
        _insert_legacy_trade_with_position_lineage(store, legacy_run)
        session.flush()

        before = capture_environment_snapshot(store)
        report = execute_rollout(store, dry_run=True)
        after = capture_environment_snapshot(store)
        assert snapshots_equal(before, after)
        assert report["actual_db_changes"] == 0
        assert report["research_portfolios"] == 280
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_execute_rollout_archives_preserves_history_and_starts_clean(rollout_db, monkeypatch):
    session = SessionLocal()
    store = TradingStore(session)
    legacy_run = str(uuid.uuid4())
    monkeypatch.setattr(rollout, "EXPECTED_LEGACY_RUN_ID", legacy_run)
    try:
        _seed_multi_strategy_if_needed(store)
        _enable_full_competition(store)
        _ensure_research_account(store)
        _ensure_live_sim_account(store)
        create_paper_run(
            store,
            starting_broker_cash=Decimal("320000"),
            execution_model="legacy_spot_limited",
            run_id=legacy_run,
        )
        _insert_legacy_trade_with_position_lineage(store, legacy_run)
        session.flush()

        result = execute_rollout(store, dry_run=False)
        session.flush()

        archived = result["archived_run"]
        new_run = result["new_run"]
        assert archived == legacy_run
        assert get_current_paper_run_id(store) == new_run
        assert count_scoped_closed_trades(store, archived) == 1
        assert count_scoped_closed_trades(store, new_run) == 0
        assert result["new_run_intents"] == 0
        assert active_research_portfolio_counts(store).total == 280

        research = store.session.execute(
            text("SELECT execution_model::text FROM broker_accounts WHERE slug='quantara_paper_competition'")
        ).scalar()
        live = store.session.execute(
            text("SELECT execution_model::text FROM broker_accounts WHERE slug='live-sim-10k'")
        ).scalar()
        assert research == "realistic_broker_v1"
        assert live == "realistic_broker_v1"

        with pytest.raises(RolloutError) as exc:
            validate_rollout_preconditions(store)
        assert exc.value.code == "already_rolled_out"
    finally:
        session.rollback()
        session.close()


def test_rollout_script_imports_and_dry_run_flag():
    root = __import__("pathlib").Path(__file__).resolve().parents[3]
    script = root / "scripts" / "rollout_realistic_broker_v1.py"
    assert script.exists()
    ns: dict = {}
    exec(script.read_text(encoding="utf-8"), {"__name__": "rollout_test", "__file__": str(script)}, ns)
    assert callable(ns["main"])
