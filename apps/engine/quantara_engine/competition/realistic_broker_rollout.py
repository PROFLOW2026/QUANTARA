"""Research execution-model rollout: legacy_spot_limited → realistic_broker_v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.competition.multi_strategy_constants import (
    MEAN_REVERSION_PORTFOLIOS,
    MOMENTUM_CONTINUATION_PORTFOLIOS,
    VOLATILITY_SQUEEZE_PORTFOLIOS,
)
from quantara_engine.competition.orb_constants import ORB_COMPETITION_PORTFOLIOS
from quantara_engine.competition.paper_run import (
    PAPER_RUN_STATUS_ACTIVE,
    PAPER_RUN_STATUS_LEGACY,
    count_scoped_closed_trades,
    create_paper_run,
    end_paper_run,
    get_current_paper_run_id,
    paper_run_columns_ready,
)
from quantara_engine.persistence.store import TradingStore

EXPECTED_LEGACY_RUN_ID = "681988b3-36e7-48dc-9354-7582e96d37ce"
TARGET_EXECUTION_MODEL = "realistic_broker_v1"
LEGACY_EXECUTION_MODEL = "legacy_spot_limited"
LIVE_SIM_PRISTINE_CASH = Decimal("10000")


class RolloutError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResearchPortfolioCounts:
    total: int
    robot_a: int
    robot_b: int
    robot_c: int
    robot_d: int
    robot_e: int


@dataclass(frozen=True)
class EnvironmentSnapshot:
    active_run_count: int
    current_run_id: str | None
    current_execution_model: str | None
    research_execution_model: str | None
    live_execution_model: str | None
    research_closed_trades: int
    research_broker_orders: int
    research_broker_fills: int
    research_realized_pnl: str
    live_cash: str
    live_equity: str
    live_fills: int
    live_positions: int
    live_rejected_orders: int


def canonical_research_portfolio_counts() -> ResearchPortfolioCounts:
    return ResearchPortfolioCounts(
        total=(
            len(ACTIVE_COMPETITION_PORTFOLIOS)
            + len(ORB_COMPETITION_PORTFOLIOS)
            + len(MEAN_REVERSION_PORTFOLIOS)
            + len(VOLATILITY_SQUEEZE_PORTFOLIOS)
            + len(MOMENTUM_CONTINUATION_PORTFOLIOS)
        ),
        robot_a=len(ACTIVE_COMPETITION_PORTFOLIOS),
        robot_b=len(ORB_COMPETITION_PORTFOLIOS),
        robot_c=len(MEAN_REVERSION_PORTFOLIOS),
        robot_d=len(VOLATILITY_SQUEEZE_PORTFOLIOS),
        robot_e=len(MOMENTUM_CONTINUATION_PORTFOLIOS),
    )


def active_research_portfolio_counts(store: TradingStore) -> ResearchPortfolioCounts:
    robot_a, robot_b, combined = store.list_all_competition_entries()
    cde = store.list_multi_strategy_competition_entries()
    robot_c = [e for e in cde if e["instance"].strategy_slug == "mean-reversion"]
    robot_d = [e for e in cde if e["instance"].strategy_slug == "volatility-squeeze"]
    robot_e = [e for e in cde if e["instance"].strategy_slug == "momentum-continuation"]
    return ResearchPortfolioCounts(
        total=len(combined),
        robot_a=len(robot_a),
        robot_b=len(robot_b),
        robot_c=len(robot_c),
        robot_d=len(robot_d),
        robot_e=len(robot_e),
    )


def _account_row(store: TradingStore, slug: str) -> dict[str, Any]:
    row = store.session.execute(
        text(
            """
            SELECT cash, balance, equity, realized_pnl, spot_crypto_cash,
                   execution_model::text AS execution_model
            FROM broker_accounts
            WHERE slug = :slug
            """
        ),
        {"slug": slug},
    ).mappings().first()
    return dict(row) if row else {}


def _all_broker_orders_count(store: TradingStore) -> int:
    row = store.session.execute(text("SELECT COUNT(*)::int AS c FROM broker_orders")).scalar()
    return int(row or 0)


def _all_broker_fills_count(store: TradingStore) -> int:
    row = store.session.execute(text("SELECT COUNT(*)::int AS c FROM broker_fills")).scalar()
    return int(row or 0)


def _broker_order_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_orders o
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE ba.slug = :slug
            """
        ),
        {"slug": account_slug},
    ).scalar()
    return int(row or 0)


def _broker_fill_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE ba.slug = :slug
            """
        ),
        {"slug": account_slug},
    ).scalar()
    return int(row or 0)


def _open_position_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            WHERE ba.slug = :slug AND bp.net_quantity != 0
            """
        ),
        {"slug": account_slug},
    ).scalar()
    return int(row or 0)


def _rejected_order_count(store: TradingStore, account_slug: str) -> int:
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c
            FROM broker_orders o
            JOIN broker_accounts ba ON ba.id = o.broker_account_id
            WHERE ba.slug = :slug AND o.status = 'rejected'
            """
        ),
        {"slug": account_slug},
    ).scalar()
    return int(row or 0)


def _run_execution_model(store: TradingStore, run_id: str | None) -> str | None:
    if not run_id:
        return None
    row = store.session.execute(
        text("SELECT execution_model::text FROM paper_runs WHERE id = :id"),
        {"id": run_id},
    ).scalar()
    return str(row) if row else None


def _active_run_count(store: TradingStore) -> int:
    row = store.session.execute(
        text("SELECT COUNT(*)::int FROM paper_runs WHERE status = :st"),
        {"st": PAPER_RUN_STATUS_ACTIVE},
    ).scalar()
    return int(row or 0)


def capture_environment_snapshot(store: TradingStore) -> EnvironmentSnapshot:
    current_run = get_current_paper_run_id(store)
    research = _account_row(store, RESEARCH_PAPER_ACCOUNT_SLUG)
    live = _account_row(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    return EnvironmentSnapshot(
        active_run_count=_active_run_count(store),
        current_run_id=current_run,
        current_execution_model=_run_execution_model(store, current_run),
        research_execution_model=research.get("execution_model"),
        live_execution_model=live.get("execution_model"),
        research_closed_trades=count_scoped_closed_trades(store, current_run),
        research_broker_orders=_all_broker_orders_count(store),
        research_broker_fills=_all_broker_fills_count(store),
        research_realized_pnl=str(research.get("realized_pnl", "0")),
        live_cash=str(live.get("cash", "0")),
        live_equity=str(live.get("equity", "0")),
        live_fills=_broker_fill_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
        live_positions=_open_position_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
        live_rejected_orders=_rejected_order_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
    )


def live_sim_in_place_upgrade_eligible(store: TradingStore) -> bool:
    live = _account_row(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    if not live:
        return False
    cash = Decimal(str(live.get("cash", "0")))
    equity = Decimal(str(live.get("equity", "0")))
    spot = Decimal(str(live.get("spot_crypto_cash", cash)))
    realized = Decimal(str(live.get("realized_pnl", "0")))
    return (
        cash == LIVE_SIM_PRISTINE_CASH
        and equity == LIVE_SIM_PRISTINE_CASH
        and spot == LIVE_SIM_PRISTINE_CASH
        and realized == Decimal("0")
        and _broker_fill_count(store, LIVE_SIM_10K_ACCOUNT_SLUG) == 0
        and _open_position_count(store, LIVE_SIM_10K_ACCOUNT_SLUG) == 0
    )


def validate_rollout_preconditions(store: TradingStore) -> None:
    research = _account_row(store, RESEARCH_PAPER_ACCOUNT_SLUG)
    if research.get("execution_model") == TARGET_EXECUTION_MODEL:
        raise RolloutError("already_rolled_out", "Research account already uses realistic_broker_v1")

    current_run = get_current_paper_run_id(store)
    if not current_run:
        raise RolloutError("no_active_run", "No active paper run to archive")

    if current_run != EXPECTED_LEGACY_RUN_ID:
        if research.get("execution_model") == TARGET_EXECUTION_MODEL:
            raise RolloutError("already_rolled_out", "Rollout already completed")
        raise RolloutError(
            "unexpected_current_run",
            f"Expected active run {EXPECTED_LEGACY_RUN_ID}, found {current_run}",
        )

    current_model = _run_execution_model(store, current_run)
    if current_model == TARGET_EXECUTION_MODEL:
        raise RolloutError("already_rolled_out", "Active paper run already uses realistic_broker_v1")

    canonical = canonical_research_portfolio_counts()
    active = active_research_portfolio_counts(store)
    if active.total != canonical.total:
        raise RolloutError(
            "portfolio_count_mismatch",
            f"Active competition portfolios={active.total}, expected canonical total={canonical.total}",
        )


def build_dry_run_report(store: TradingStore) -> dict[str, Any]:
    current_run = get_current_paper_run_id(store)
    research = _account_row(store, RESEARCH_PAPER_ACCOUNT_SLUG)
    live = _account_row(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    canonical = canonical_research_portfolio_counts()
    active = active_research_portfolio_counts(store)
    closed_for_run = count_scoped_closed_trades(store, current_run)
    live_eligible = live_sim_in_place_upgrade_eligible(store)

    return {
        "current_run": current_run,
        "current_execution_model": research.get("execution_model", LEGACY_EXECUTION_MODEL),
        "will_archive_current_run": current_run is not None,
        "historical_closed_trades_preserved": closed_for_run,
        "historical_orders_preserved": _all_broker_orders_count(store),
        "historical_fills_preserved": _all_broker_fills_count(store),
        "historical_short_denials_replayed": 0,
        "new_run_execution_model": TARGET_EXECUTION_MODEL,
        "research_portfolios": canonical.total,
        "robot_A_portfolios": canonical.robot_a,
        "robot_B_portfolios": canonical.robot_b,
        "robot_C_portfolios": canonical.robot_c,
        "robot_D_portfolios": canonical.robot_d,
        "robot_E_portfolios": canonical.robot_e,
        "active_research_portfolios": active.total,
        "new_run_starts_clean": True,
        "old_run_remains_queryable": True,
        "live_sim_cash": str(live.get("cash", "unknown")),
        "live_sim_equity": str(live.get("equity", "unknown")),
        "live_sim_fills": _broker_fill_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
        "live_sim_open_positions": _open_position_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
        "live_sim_historical_rejected_orders": _rejected_order_count(store, LIVE_SIM_10K_ACCOUNT_SLUG),
        "live_sim_in_place_upgrade_eligible": live_eligible,
        "dry_run": True,
        "actual_db_changes": 0,
    }


def _count_scoped_intents(store: TradingStore, run_id: str) -> int:
    if not paper_run_columns_ready(store):
        return 0
    row = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM order_intents
            WHERE paper_run_id = CAST(:run_id AS uuid)
            """
        ),
        {"run_id": run_id},
    ).scalar()
    return int(row or 0)


def execute_rollout(store: TradingStore, *, dry_run: bool = False) -> dict[str, Any]:
    before = capture_environment_snapshot(store)
    validate_rollout_preconditions(store)
    report = build_dry_run_report(store)

    if dry_run:
        report["snapshot_before"] = before.__dict__
        report["snapshot_after"] = before.__dict__
        return report

    current_run = report["current_run"]
    assert current_run

    end_paper_run(store, current_run)
    store.session.execute(
        text(
            """
            UPDATE paper_runs
            SET ended_reason = 'execution_model_upgrade',
                execution_model = CAST(:model AS execution_model_version)
            WHERE id = :id
            """
        ),
        {"id": current_run, "model": LEGACY_EXECUTION_MODEL},
    )

    new_run = create_paper_run(
        store,
        starting_broker_cash=Decimal("320000"),
        metadata={"execution_model": TARGET_EXECUTION_MODEL, "reason": "execution_model_upgrade"},
        execution_model=TARGET_EXECUTION_MODEL,
    )

    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET execution_model = CAST(:model AS execution_model_version)
            WHERE slug = :slug
            """
        ),
        {"slug": RESEARCH_PAPER_ACCOUNT_SLUG, "model": TARGET_EXECUTION_MODEL},
    )

    live_upgraded = False
    if report["live_sim_in_place_upgrade_eligible"]:
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET execution_model = CAST(:model AS execution_model_version)
                WHERE slug = :slug
                """
            ),
            {"slug": LIVE_SIM_10K_ACCOUNT_SLUG, "model": TARGET_EXECUTION_MODEL},
        )
        live_upgraded = True

    after = capture_environment_snapshot(store)
    archived_closed = count_scoped_closed_trades_for_run(store, current_run)
    new_run_closed = count_scoped_closed_trades(store, new_run)
    new_run_intents = _count_scoped_intents(store, new_run)

    return {
        "archived_run": current_run,
        "new_run": new_run,
        "archived_closed_trades": archived_closed,
        "new_run_closed_trades": new_run_closed,
        "new_run_intents": new_run_intents,
        "live_sim_upgraded": live_upgraded,
        "research_portfolios": active_research_portfolio_counts(store).total,
        "snapshot_before": before.__dict__,
        "snapshot_after": after.__dict__,
    }


def count_scoped_closed_trades_for_run(store: TradingStore, run_id: str) -> int:
    """Count closed trades for an archived run (explicit run id, not current setting)."""
    return count_scoped_closed_trades(store, run_id)


def snapshots_equal(a: EnvironmentSnapshot, b: EnvironmentSnapshot) -> bool:
    return a.__dict__ == b.__dict__


def rollout_report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)
