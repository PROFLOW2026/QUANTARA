"""TradingStore — SQLAlchemy-backed persistence for engine domain objects."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantara_engine.domain.types import (
    Candle as DomainCandle,
    DecisionLogEntry,
    Direction,
    ExitReason,
    Fill,
    Instrument,
    IntentStatus,
    Mode,
    Order,
    OrderIntent,
    OrderStatus,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
    Trade,
    new_id,
)
from quantara_engine.competition.constants import (
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_NAME_HE,
    COMPETITION_TOTAL_INITIAL,
    PORTFOLIO_DEF_BY_ID,
)
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.egress_metrics import EgressMetrics
from quantara_engine.models.enums import (
    coerce_worker_run_status,
    BacktestStatus,
    DecisionType as OrmDecisionType,
    Direction as OrmDirection,
    EntryType,
    ExitReason as OrmExitReason,
    FillSide,
    JobType,
    OrderIntentStatus,
    OrderStatus as OrmOrderStatus,
    OrderType,
    PortfolioMode,
    PortfolioStatus as OrmPortfolioStatus,
    PositionStatus as OrmPositionStatus,
    RiskProfileSlug,
    SignalAction as OrmSignalAction,
    WorkerJobStatus,
    WorkerRunStatus,
)
from quantara_engine.models.instruments import Candle as OrmCandle
from quantara_engine.models.instruments import Instrument as OrmInstrument
from quantara_engine.models.portfolio import Experiment as OrmExperiment
from quantara_engine.models.portfolio import BacktestRun as OrmBacktestRun
from quantara_engine.models.portfolio import Portfolio as OrmPortfolio
from quantara_engine.models.portfolio import PortfolioSnapshot as OrmPortfolioSnapshot
from quantara_engine.models.portfolio import RiskProfile as OrmRiskProfile
from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance
from quantara_engine.models.strategies import Strategy as OrmStrategy
from quantara_engine.models.strategies import StrategyVersion as OrmStrategyVersion
from quantara_engine.models.trading import Decision as OrmDecision
from quantara_engine.models.trading import Fill as OrmFill
from quantara_engine.models.trading import Order as OrmOrder
from quantara_engine.models.trading import OrderIntent as OrmOrderIntent
from quantara_engine.models.trading import Position as OrmPosition
from quantara_engine.models.trading import Signal as OrmSignal
from quantara_engine.models.trading import Trade as OrmTrade
from quantara_engine.models.workers import Event as OrmEvent
from quantara_engine.models.workers import Setting as OrmSetting
from quantara_engine.models.workers import WorkerJob as OrmWorkerJob
from quantara_engine.models.workers import WorkerRun as OrmWorkerRun
from quantara_engine.portfolio.service import PortfolioSnapshot, PortfolioState


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not value or not str(value).strip():
        raise ValueError("UUID value is required")
    return uuid.UUID(str(value))


def _is_uuid(value: str | uuid.UUID | None) -> bool:
    if isinstance(value, uuid.UUID):
        return True
    if not value or not str(value).strip():
        return False
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _str_id(value: uuid.UUID | str | None) -> str:
    if value is None:
        return ""
    return str(value)


def _mode_to_orm(mode: Mode) -> PortfolioMode:
    return PortfolioMode(mode.value)


def _mode_from_orm(mode: PortfolioMode) -> Mode:
    return Mode(mode.value)


OWNER_ID = "00000000-0000-0000-0000-000000000001"


def _intent_idempotency_key(
    strategy_instance_id: str,
    signal_candle_timestamp: datetime,
    direction: str,
    *,
    opportunity_key: str | None = None,
) -> str:
    """One intent per portfolio instance + canonical opportunity (preferred) or signal candle."""
    if opportunity_key:
        from quantara_engine.risk.opportunity import opportunity_idempotency_key

        return opportunity_idempotency_key(strategy_instance_id, opportunity_key)
    raw = f"{strategy_instance_id}:{signal_candle_timestamp.isoformat()}:{direction}"
    return hashlib.sha256(raw.encode()).hexdigest()


class TradingStore:
    """Persistence facade over SQLAlchemy session and ORM models."""

    def __init__(
        self,
        session: Session,
        mode: Mode = Mode.PAPER,
        backtest_run_id: str | None = None,
    ) -> None:
        self.session = session
        self.mode = mode
        self.backtest_run_id = backtest_run_id
        self._settings_cache: dict[str, Any] | None = None
        self._competition_entries_cache: (
            tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None
        ) = None
        self.egress_metrics: EgressMetrics | None = None

    def flush(self) -> None:
        self.session.flush()

    def commit(self) -> None:
        self.session.commit()

    def _bt_uuid(self) -> uuid.UUID | None:
        if self.backtest_run_id:
            return _uuid(self.backtest_run_id)
        return None

    def _paper_run_uuid(self) -> uuid.UUID | None:
        if self.backtest_run_id:
            return None
        from quantara_engine.competition.paper_run import paper_run_uuid

        return paper_run_uuid(self)

    def list_competition_portfolios(self) -> list[Portfolio]:
        return [entry["portfolio"] for entry in self.list_competition_entries()]

    def count_open_competition_positions(self) -> int:
        """Open paper positions across Robot A + Robot B competition portfolios."""
        from quantara_engine.competition.paper_run import position_scope_clause

        _, _, entries = self.list_all_competition_entries()
        pids = [_uuid(e["portfolio"].id) for e in entries]
        if not pids:
            return 0
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(OrmPosition)
                .where(
                    OrmPosition.portfolio_id.in_(pids),
                    OrmPosition.status == OrmPositionStatus.OPEN,
                    position_scope_clause(self),
                )
            )
            or 0
        )

    def _competition_combined_portfolio(self, entries: list[dict[str, Any]] | None = None) -> Portfolio:
        """Synthetic aggregate over active competition portfolios."""
        entries = entries if entries is not None else self.list_competition_entries()
        if not entries:
            raise ValueError("No active competition portfolios")

        portfolios = [entry["portfolio"] for entry in entries]
        equity = sum((p.equity for p in portfolios), Decimal("0"))
        balance = sum((p.balance for p in portfolios), Decimal("0"))
        unrealized = sum((p.unrealized_pnl for p in portfolios), Decimal("0"))
        exposure = sum((p.exposure_notional for p in portfolios), Decimal("0"))
        peak = sum((p.peak_equity for p in portfolios), Decimal("0"))
        status = (
            PortfolioStatus.HALTED
            if any(p.status == PortfolioStatus.HALTED for p in portfolios)
            else PortfolioStatus.ACTIVE
        )

        return Portfolio(
            id=ACTIVE_COMPETITION_EXPERIMENT_ID,
            name=COMPETITION_NAME_HE,
            mode=Mode.PAPER,
            initial_capital=COMPETITION_TOTAL_INITIAL,
            balance=balance,
            unrealized_pnl=unrealized,
            equity=equity,
            exposure_notional=exposure,
            reserved_capital=Decimal("0"),
            currency="USD",
            status=status,
            peak_equity=peak,
        )

    def resolve_paper_portfolio(self, ref: str = "competition") -> Portfolio:
        """Resolve a competition portfolio or the combined experiment aggregate."""
        entries = self.list_competition_entries()
        if not entries:
            raise ValueError("No active competition portfolios")

        slug = ref.lower().replace("_", "-")
        if slug in ("competition", "paper-main", "paper", "combined"):
            return self._competition_combined_portfolio(entries)

        try:
            uid = _str_id(_uuid(ref))
            for entry in entries:
                if entry["portfolio"].id == uid:
                    return entry["portfolio"]
        except ValueError:
            pass

        raise ValueError(f"Unknown portfolio ref: {ref}")

    def sum_competition_realized_pnl(self) -> Decimal:
        from quantara_engine.persistence.batch_summary import sum_realized_pnl_for_portfolios

        _, _, combined = self.list_all_competition_entries()
        portfolio_ids = [e["portfolio"].id for e in combined]
        return sum_realized_pnl_for_portfolios(self, portfolio_ids)

    def list_competition_positions(
        self,
        *,
        open_only: bool = True,
        status: str | None = None,
    ) -> list[Position]:
        from quantara_engine.persistence.batch_summary import list_positions_for_portfolios

        _, _, combined = self.list_all_competition_entries()
        portfolio_ids = [e["portfolio"].id for e in combined]
        return list_positions_for_portfolios(
            self,
            portfolio_ids,
            open_only=open_only,
            status=status,
        )

    def list_competition_trades_all(self, *, limit: int = 500) -> list[Trade]:
        trades: list[Trade] = []
        for portfolio in self.list_competition_portfolios():
            trades.extend(self.list_trades(portfolio.id, limit=limit, paper_only=True))
        trades.sort(key=lambda row: row.closed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return trades[:limit]

    def set_competition_portfolio_status(self, status: PortfolioStatus) -> None:
        for portfolio in self.list_competition_portfolios():
            self.update_portfolio(replace(portfolio, status=status))

    def list_instruments(self) -> list[Instrument]:
        rows = self.session.scalars(
            select(OrmInstrument)
            .where(OrmInstrument.is_active.is_(True))
            .order_by(OrmInstrument.symbol)
        ).all()
        return [self._instrument_to_domain(row) for row in rows]

    def latest_candle_timestamp(
        self, instrument_id: str, timeframe: str
    ) -> datetime | None:
        return self.session.scalar(
            select(func.max(OrmCandle.timestamp)).where(
                OrmCandle.instrument_id == _uuid(instrument_id),
                OrmCandle.timeframe == timeframe,
            )
        )

    def sum_realized_pnl(self, portfolio_id: str) -> Decimal:
        total = self.session.scalar(
            select(func.coalesce(func.sum(OrmTrade.realized_pnl), 0)).where(
                OrmTrade.portfolio_id == _uuid(portfolio_id),
                OrmTrade.backtest_run_id.is_(None),
            )
        )
        return Decimal(str(total or 0))

    def get_latest_decision(self) -> DecisionLogEntry | None:
        skip_types = {
            OrmDecisionType.HOLD,
            OrmDecisionType.NO_SETUP,
        }
        competition_ids = self.list_competition_instance_ids()
        if competition_ids:
            row = self.session.scalar(
                select(OrmDecision)
                .where(
                    OrmDecision.strategy_instance_id.in_(
                        [_uuid(i) for i in competition_ids]
                    ),
                    OrmDecision.decision_type.not_in(skip_types),
                )
                .order_by(OrmDecision.created_at.desc())
                .limit(1)
            )
            if row:
                return self._decision_to_domain(row)

        row = self.session.scalar(
            select(OrmDecision)
            .where(OrmDecision.decision_type.not_in(skip_types))
            .order_by(OrmDecision.created_at.desc())
            .limit(1)
        )
        return self._decision_to_domain(row) if row else None

    def list_experiments(self) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(OrmExperiment).order_by(OrmExperiment.created_at.desc())
        ).all()
        return [
            {
                "id": _str_id(row.id),
                "name": row.name,
                "status": row.status.value,
                "start_date": row.start_date.isoformat() if row.start_date else None,
                "end_date": row.end_date.isoformat() if row.end_date else None,
            }
            for row in rows
        ]

    def list_backtest_trades(self, run_id: str) -> list[Trade]:
        rows = self.session.scalars(
            select(OrmTrade)
            .where(OrmTrade.backtest_run_id == _uuid(run_id))
            .order_by(OrmTrade.closed_at)
        ).all()
        return [self._trade_to_domain(row) for row in rows]

    def count_trades_for_version(self, strategy_version_id: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(OrmTrade)
            .where(
                OrmTrade.strategy_version_id == _uuid(strategy_version_id),
                OrmTrade.backtest_run_id.is_(None),
            )
        ) or 0

    def count_backtests_for_version(self, strategy_version_id: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(OrmBacktestRun)
            .where(OrmBacktestRun.strategy_version_id == _uuid(strategy_version_id))
        ) or 0

    def count_active_instances(self, strategy_slug: str) -> int:
        stmt = (
            select(func.count())
            .select_from(OrmStrategyInstance)
            .join(
                OrmStrategyVersion,
                OrmStrategyInstance.strategy_version_id == OrmStrategyVersion.id,
            )
            .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
            .where(
                OrmStrategy.slug == strategy_slug,
                OrmStrategyInstance.is_active.is_(True),
            )
        )
        return self.session.scalar(stmt) or 0

    def get_strategy_version_by_slug(self, slug: str, version: str) -> dict[str, Any] | None:
        return self.get_strategy_version(slug, version)

    def resolve_strategy_version_ref(self, ref: str) -> dict[str, Any] | None:
        try:
            row = self.session.get(OrmStrategyVersion, _uuid(ref))
            if row:
                strategy = self.session.get(OrmStrategy, row.strategy_id)
                return {
                    "id": _str_id(row.id),
                    "strategy_id": _str_id(row.strategy_id),
                    "version": row.version,
                    "parameters": row.parameters,
                    "logic_hash": row.logic_hash,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "strategy_name": strategy.name if strategy else "Unknown",
                    "strategy_slug": strategy.slug if strategy else "",
                }
        except ValueError:
            pass
        if ref in ("gtp-v1", "gold-trend-pullback-1.0.0"):
            version = self.get_strategy_version("gold-trend-pullback", "1.0.0")
            if version:
                strategy = self.get_strategy_by_slug("gold-trend-pullback")
                version["strategy_name"] = strategy["name"] if strategy else "Gold Trend Pullback"
                version["strategy_slug"] = "gold-trend-pullback"
            return version
        if ref in ("orb-v1", "opening-range-breakout-1.0.0"):
            version = self.get_strategy_version("opening-range-breakout", "1.0.0")
            if version:
                strategy = self.get_strategy_by_slug("opening-range-breakout")
                version["strategy_name"] = strategy["name"] if strategy else "Opening Range Breakout"
                version["strategy_slug"] = "opening-range-breakout"
            return version
        return None

    def latest_worker_runs(self) -> dict[str, OrmWorkerRun]:
        runs: dict[str, OrmWorkerRun] = {}
        for name in ("data_fetcher", "strategy_runner", "backtest_runner", "snapshot"):
            row = self.session.scalar(
                select(OrmWorkerRun)
                .where(OrmWorkerRun.worker_name == name)
                .order_by(OrmWorkerRun.started_at.desc())
                .limit(1)
            )
            if row:
                runs[name] = row
        return runs

    def update_settings_bulk(self, payload: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "timezone": "display_timezone",
            "default_risk_profile": "default_risk_profile",
            "paper_trading_enabled": "paper_trading_enabled",
            "initial_capital": "default_initial_capital",
        }
        for api_key, value in payload.items():
            if api_key == "trading_halted":
                self.update_settings("paper_trading_enabled", not value)
                continue
            if api_key == "execution_defaults" and isinstance(value, dict):
                if "spread" in value:
                    self.update_settings("paper_default_spread", value["spread"])
                if "slippage" in value:
                    self.update_settings("paper_default_slippage_pct", value["slippage"])
                if "fees" in value:
                    self.update_settings("paper_default_fee_rate", value["fees"])
                continue
            db_key = mapping.get(api_key, api_key)
            self.update_settings(db_key, value)
        return self.get_settings_dict()

    def settings_api_response(self) -> dict[str, Any]:
        s = self.get_settings_dict()
        paper_enabled = s.get("paper_trading_enabled", True)
        return {
            "timezone": s.get("display_timezone", "Asia/Jerusalem"),
            "default_risk_profile": s.get("default_risk_profile", "balanced"),
            "paper_trading_enabled": paper_enabled,
            "initial_capital": s.get("default_initial_capital", 10000),
            "trading_halted": not paper_enabled,
            "trading_control": self.get_settings_dict().get("trading_control_state") or {"state": "running"},
            "execution_defaults": {
                "spread": s.get("paper_default_spread", 0.30),
                "slippage": s.get("paper_default_slippage_pct", 0.0001),
                "fees": s.get("paper_default_fee_rate", 0),
            },
            "api_key_display": "***",
            "language": "he",
        }

    # ------------------------------------------------------------------ Reference

    def get_instrument_by_symbol(self, symbol: str) -> Instrument | None:
        row = self.session.scalar(
            select(OrmInstrument).where(OrmInstrument.symbol == symbol)
        )
        return self._instrument_to_domain(row) if row else None

    def get_instrument_by_id(self, instrument_id: str) -> Instrument | None:
        row = self.session.get(OrmInstrument, _uuid(instrument_id))
        return self._instrument_to_domain(row) if row else None

    def resolve_jpy_per_usd(self) -> Decimal:
        """Canonical JPY per 1 USD from hourly shared cache (no per-call provider access)."""
        from quantara_engine.portfolio.fx_rate_cache import get_canonical_jpy_per_usd

        return get_canonical_jpy_per_usd(self)

    def ensure_usdjpy_conversion_instrument(self) -> None:
        from quantara_engine.portfolio.currency import USDJPY_DB_SYMBOL, usdjpy_instrument_row

        existing = self.session.scalar(
            select(OrmInstrument).where(OrmInstrument.symbol == USDJPY_DB_SYMBOL)
        )
        if existing:
            return
        meta = usdjpy_instrument_row()
        self.session.add(
            OrmInstrument(
                id=meta["id"],
                symbol=meta["symbol"],
                name=meta["name"],
                asset_class=meta["asset_class"],
                base_currency=meta["base_currency"],
                quote_currency=meta["quote_currency"],
                pip_size=Decimal("0.01"),
                contract_size=Decimal("1"),
                price_tick_size=Decimal("0.001"),
                quantity_step=Decimal("1000"),
                min_quantity=Decimal("1000"),
                is_active=False,
            )
        )
        self.session.flush()

    def fix_gbpjpy_instrument_metadata(self) -> bool:
        row = self.session.scalar(
            select(OrmInstrument).where(OrmInstrument.symbol == "GBPJPY")
        )
        if not row:
            return False
        changed = False
        if row.base_currency != "GBP":
            row.base_currency = "GBP"
            changed = True
        if row.quote_currency != "JPY":
            row.quote_currency = "JPY"
            changed = True
        if changed:
            self.session.flush()
        return changed

    def build_currency_context_for_instruments(
        self, instruments: list[Instrument]
    ) -> "CurrencyContext":
        from quantara_engine.portfolio.currency import build_currency_context

        return build_currency_context(self, instruments)

    def get_active_strategy_instance(self, portfolio_id: str) -> StrategyInstance | None:
        row = self.session.scalar(
            select(OrmStrategyInstance).where(
                OrmStrategyInstance.portfolio_id == _uuid(portfolio_id),
                OrmStrategyInstance.is_active.is_(True),
            )
        )
        if not row:
            return None
        version = self.session.get(OrmStrategyVersion, row.strategy_version_id)
        strategy = (
            self.session.get(OrmStrategy, version.strategy_id) if version else None
        )
        slug = self.resolve_strategy_slug(row)
        return self._strategy_instance_to_domain(row, slug or "gold-trend-pullback")

    def resolve_strategy_slug(
        self, instance: OrmStrategyInstance | str
    ) -> str | None:
        """Canonical strategy slug via StrategyInstance → StrategyVersion → Strategy."""
        row = instance
        if isinstance(instance, str):
            row = self.session.get(OrmStrategyInstance, _uuid(instance))
        if not row:
            return None
        version = self.session.get(OrmStrategyVersion, row.strategy_version_id)
        if not version:
            return None
        strategy = self.session.get(OrmStrategy, version.strategy_id)
        return strategy.slug if strategy else None

    def get_risk_profile_by_slug(self, slug: str) -> RiskProfile | None:
        try:
            slug_enum = RiskProfileSlug(slug)
        except ValueError:
            return None
        row = self.session.scalar(
            select(OrmRiskProfile).where(OrmRiskProfile.slug == slug_enum)
        )
        return self._risk_profile_to_domain(row) if row else None

    def get_risk_profile_by_id(self, profile_id: str) -> RiskProfile | None:
        row = self.session.get(OrmRiskProfile, _uuid(profile_id))
        return self._risk_profile_to_domain(row) if row else None

    def get_competition_experiment_id(self) -> str | None:
        settings = self.get_settings_dict()
        exp_id = settings.get("competition_experiment_id")
        return str(exp_id) if exp_id else None

    def get_competition_started_at(self) -> datetime | None:
        settings = self.get_settings_dict()
        raw = settings.get("competition_started_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    def list_competition_entries(
        self,
        *,
        experiment_id: str | None = None,
        strategy_slug: str = "gold-trend-pullback",
        order_map: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Active competition portfolios with strategy instance and risk profile."""
        exp_id = experiment_id or self.get_competition_experiment_id()
        if not exp_id:
            return []

        if order_map is None:
            order_map = {p.portfolio_id: p.sort_order for p in ACTIVE_COMPETITION_PORTFOLIOS}
        stmt = (
            select(
                OrmStrategyInstance,
                OrmPortfolio,
                OrmRiskProfile,
                OrmStrategy,
                OrmStrategyVersion,
            )
            .join(OrmPortfolio, OrmStrategyInstance.portfolio_id == OrmPortfolio.id)
            .join(OrmRiskProfile, OrmStrategyInstance.risk_profile_id == OrmRiskProfile.id)
            .join(
                OrmStrategyVersion,
                OrmStrategyInstance.strategy_version_id == OrmStrategyVersion.id,
            )
            .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
            .where(
                OrmStrategyInstance.experiment_id == _uuid(exp_id),
                OrmStrategyInstance.is_active.is_(True),
                OrmStrategy.slug == strategy_slug,
            )
        )
        rows = self.session.execute(stmt).all()
        entries: list[dict[str, Any]] = []
        for instance_row, portfolio_row, risk_row, strategy_row, version_row in rows:
            entries.append(
                {
                    "portfolio": self._portfolio_to_domain(portfolio_row),
                    "instance": self._strategy_instance_to_domain(
                        instance_row,
                        strategy_row.slug,
                        version_row=version_row,
                    ),
                    "risk_profile": self._risk_profile_to_domain(risk_row),
                    "sort_order": order_map.get(_str_id(portfolio_row.id), 99),
                }
            )
        entries.sort(key=lambda e: e["sort_order"])
        return entries

    def is_orb_competition_enabled(self) -> bool:
        settings = self.get_settings_dict()
        return bool(settings.get("orb_competition_enabled"))

    def is_multi_strategy_competition_enabled(self, strategy_slug: str | None = None) -> bool:
        from quantara_engine.competition.multi_strategy_constants import SETTINGS_BY_STRATEGY

        settings = self.get_settings_dict()
        if strategy_slug:
            key = SETTINGS_BY_STRATEGY.get(strategy_slug)
            return bool(key and settings.get(key))
        return any(bool(settings.get(key)) for key in SETTINGS_BY_STRATEGY.values())

    def list_multi_strategy_competition_entries(
        self, strategy_slug: str | None = None
    ) -> list[dict[str, Any]]:
        from quantara_engine.competition.multi_strategy_constants import (
            MEAN_REVERSION_EXPERIMENT_ID,
            MEAN_REVERSION_STRATEGY_SLUG,
            MOMENTUM_CONTINUATION_EXPERIMENT_ID,
            MOMENTUM_CONTINUATION_STRATEGY_SLUG,
            PORTFOLIO_DEF_BY_ID,
            VOLATILITY_SQUEEZE_EXPERIMENT_ID,
            VOLATILITY_SQUEEZE_STRATEGY_SLUG,
        )

        specs = [
            (MEAN_REVERSION_EXPERIMENT_ID, MEAN_REVERSION_STRATEGY_SLUG),
            (VOLATILITY_SQUEEZE_EXPERIMENT_ID, VOLATILITY_SQUEEZE_STRATEGY_SLUG),
            (MOMENTUM_CONTINUATION_EXPERIMENT_ID, MOMENTUM_CONTINUATION_STRATEGY_SLUG),
        ]
        entries: list[dict[str, Any]] = []
        for experiment_id, slug in specs:
            if strategy_slug and slug != strategy_slug:
                continue
            if not self.is_multi_strategy_competition_enabled(slug):
                continue
            entries.extend(
                self.list_competition_entries(
                    experiment_id=experiment_id,
                    strategy_slug=slug,
                    order_map={pid: d.sort_order for pid, d in PORTFOLIO_DEF_BY_ID.items()},
                )
            )
        return entries

    def list_orb_competition_entries(self) -> list[dict[str, Any]]:
        from quantara_engine.competition.orb_constants import (
            ORB_COMPETITION_EXPERIMENT_ID,
            ORB_PORTFOLIO_DEF_BY_ID,
            ORB_STRATEGY_SLUG,
        )

        if not self.is_orb_competition_enabled():
            return []
        return self.list_competition_entries(
            experiment_id=ORB_COMPETITION_EXPERIMENT_ID,
            strategy_slug=ORB_STRATEGY_SLUG,
            order_map={pid: d.sort_order for pid, d in ORB_PORTFOLIO_DEF_BY_ID.items()},
        )

    def count_trades_on_session_date(
        self,
        portfolio_id: str,
        instrument_id: str,
        session_date,
    ) -> int:
        """Count trades opened on a US RTH session date (ET calendar day)."""
        from datetime import time as dt_time

        from quantara_engine.market_data.sessions import US_EASTERN

        start_et = datetime.combine(session_date, dt_time(0, 0), tzinfo=US_EASTERN).astimezone(
            timezone.utc
        )
        end_et = start_et + timedelta(days=1)
        count = self.session.scalar(
            select(func.count())
            .select_from(OrmTrade)
            .where(
                OrmTrade.portfolio_id == _uuid(portfolio_id),
                OrmTrade.instrument_id == _uuid(instrument_id),
                OrmTrade.opened_at >= start_et,
                OrmTrade.opened_at < end_et,
                OrmTrade.backtest_run_id.is_(None),
            )
        )
        return int(count or 0)

    def list_all_competition_entries(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (robot_a, robot_b, combined) active paper competition entries."""
        if self._competition_entries_cache is not None:
            return self._competition_entries_cache
        robot_a = self.list_competition_entries()
        robot_b = self.list_orb_competition_entries() if self.is_orb_competition_enabled() else []
        robot_cde = self.list_multi_strategy_competition_entries()
        self._competition_entries_cache = (robot_a, robot_b, robot_a + robot_b + robot_cde)
        return self._competition_entries_cache

    def batch_portfolio_dashboard_stats(
        self, portfolio_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Bulk trade + open-position metrics for dashboard endpoints."""
        from quantara_engine.persistence.batch_summary import (
            TradeBatchMetrics,
            batch_open_positions_by_portfolio,
            batch_trade_metrics,
        )

        trade_stats = batch_trade_metrics(self, portfolio_ids)
        open_by_portfolio = batch_open_positions_by_portfolio(self, portfolio_ids)
        out: dict[str, dict[str, Any]] = {}
        for pid in portfolio_ids:
            trades: TradeBatchMetrics = trade_stats.get(pid, TradeBatchMetrics())
            open_positions = open_by_portfolio.get(pid, [])
            first = open_positions[0] if open_positions else None
            out[pid] = {
                "closed_trades_count": trades.closed_trades_count,
                "realized_pnl": trades.realized_pnl,
                "win_rate": trades.win_rate,
                "open_positions": open_positions,
                "open_positions_count": len(open_positions),
                "open_direction": first.direction if first else None,
            }
        return out

    def batch_asset_trading_metrics(self, portfolio_ids: list[str]) -> dict[str, dict[str, Any]]:
        from quantara_engine.persistence.batch_summary import batch_asset_metrics

        metrics = batch_asset_metrics(self, portfolio_ids)
        return {
            iid: {
                "open_positions": m.open_positions,
                "closed_trades": m.closed_trades,
                "realized_pnl": float(m.realized_pnl),
                "unrealized_pnl": float(m.unrealized_pnl),
                "total_pnl": float(m.realized_pnl + m.unrealized_pnl),
            }
            for iid, m in metrics.items()
        }

    def batch_competition_exposure_risk_summary(
        self,
        portfolio_ids: list[str],
        *,
        symbol_by_instrument_id: dict[str, str],
    ) -> tuple[Any, dict[str, Any]]:
        from quantara_engine.persistence.batch_summary import (
            batch_competition_exposure_risk_summary,
        )

        summary, by_instrument = batch_competition_exposure_risk_summary(
            self,
            portfolio_ids,
            symbol_by_instrument_id=symbol_by_instrument_id,
        )
        return summary, by_instrument

    def list_competition_instance_ids(self) -> list[str]:
        _, _, combined = self.list_all_competition_entries()
        return [e["instance"].id for e in combined]

    def list_decisions_for_portfolio(
        self,
        portfolio_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DecisionLogEntry]:
        instance_ids = [
            e["instance"].id
            for e in self.list_competition_entries()
            if e["portfolio"].id == portfolio_id
        ]
        if not instance_ids:
            instance = self.get_paper_strategy_instance(portfolio_id)
            if instance:
                instance_ids = [instance.id]
            else:
                return []
        stmt = (
            select(OrmDecision)
            .where(OrmDecision.strategy_instance_id.in_([_uuid(i) for i in instance_ids]))
            .order_by(OrmDecision.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.scalars(stmt).all()
        return [self._decision_to_domain(row) for row in rows]

    def list_competition_decisions(self, limit: int = 30) -> list[DecisionLogEntry]:
        instance_ids = self.list_competition_instance_ids()
        if not instance_ids:
            return []
        stmt = (
            select(OrmDecision)
            .where(OrmDecision.strategy_instance_id.in_([_uuid(i) for i in instance_ids]))
            .order_by(OrmDecision.created_at.desc())
            .limit(limit)
        )
        rows = self.session.scalars(stmt).all()
        return [self._decision_to_domain(row) for row in rows]

    def build_instance_strategy_identity_map(self) -> dict[str, dict[str, str]]:
        """Map strategy_instance_id -> robot_label, strategy_slug, strategy_name."""
        from quantara_engine.competition.robot_registry import ROBOT_LABELS
        from quantara_engine.strategies.registry import get

        mapping: dict[str, dict[str, str]] = {}
        _, _, combined = self.list_all_competition_entries()
        for entry in combined:
            inst = entry["instance"]
            slug = inst.strategy_slug
            try:
                strategy_name = get(slug, inst.strategy_version).name()
            except KeyError:
                strategy_name = slug
            mapping[inst.id] = {
                "robot_label": ROBOT_LABELS.get(slug, slug),
                "strategy_slug": slug,
                "strategy_name": strategy_name,
            }
        return mapping

    def _latest_decision_for_instances(
        self,
        instance_ids: list[str],
        instrument_id: str,
    ) -> DecisionLogEntry | None:
        if not instance_ids:
            return None
        row = self.session.scalar(
            select(OrmDecision)
            .where(
                OrmDecision.strategy_instance_id.in_([_uuid(i) for i in instance_ids]),
                OrmDecision.instrument_id == _uuid(instrument_id),
            )
            .order_by(OrmDecision.candle_timestamp.desc(), OrmDecision.created_at.desc())
            .limit(1)
        )
        return self._decision_to_domain(row) if row else None

    def _batch_latest_decisions_for_instances(
        self,
        instance_ids: list[str],
        instrument_ids: list[str],
    ) -> list[DecisionLogEntry]:
        if not instance_ids or not instrument_ids:
            return []
        from sqlalchemy import desc

        inst_uuids = [_uuid(i) for i in instance_ids]
        instrument_uuids = [_uuid(i) for i in instrument_ids]
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            rows = self.session.scalars(
                select(OrmDecision)
                .distinct(OrmDecision.instrument_id)
                .where(
                    OrmDecision.strategy_instance_id.in_(inst_uuids),
                    OrmDecision.instrument_id.in_(instrument_uuids),
                )
                .order_by(
                    OrmDecision.instrument_id,
                    desc(OrmDecision.candle_timestamp),
                    desc(OrmDecision.created_at),
                )
            ).all()
            return [self._decision_to_domain(row) for row in rows if row]

        results: list[DecisionLogEntry] = []
        for instrument_id in instrument_ids:
            decision = self._latest_decision_for_instances(instance_ids, instrument_id)
            if decision:
                results.append(decision)
        return results

    def list_latest_decisions_by_asset_timeframe(
        self,
        timeframe: str,
    ) -> list[DecisionLogEntry]:
        """Latest decision per asset/strategy for all active paper competition robots on a timeframe."""
        from quantara_engine.competition.orb_constants import ORB_ASSETS
        from quantara_engine.competition.robot_a_universe import list_robot_a_tradable_db_symbols

        results: list[DecisionLogEntry] = []

        entries_a = [
            e for e in self.list_competition_entries() if e["instance"].timeframe == timeframe
        ]
        if entries_a:
            instance_ids_a = [e["instance"].id for e in entries_a]
            instrument_ids_a: list[str] = []
            for symbol in list_robot_a_tradable_db_symbols():
                instrument = self.get_instrument_by_symbol(symbol)
                if instrument:
                    instrument_ids_a.append(instrument.id)
            results.extend(
                self._batch_latest_decisions_for_instances(instance_ids_a, instrument_ids_a)
            )

        if self.is_orb_competition_enabled():
            entries_b = [
                e
                for e in self.list_orb_competition_entries()
                if e["instance"].timeframe == timeframe
            ]
            if entries_b:
                instance_ids_b = [e["instance"].id for e in entries_b]
                instrument_ids_b: list[str] = []
                for symbol in ORB_ASSETS:
                    instrument = self.get_instrument_by_symbol(symbol)
                    if instrument:
                        instrument_ids_b.append(instrument.id)
                results.extend(
                    self._batch_latest_decisions_for_instances(instance_ids_b, instrument_ids_b)
                )

        entries_cde = [
            e
            for e in self.list_multi_strategy_competition_entries()
            if e["instance"].timeframe == timeframe
        ]
        if entries_cde:
            instance_ids_cde = [e["instance"].id for e in entries_cde]
            instrument_ids_cde: list[str] = []
            from quantara_engine.market_data.active_universe import list_active_db_symbols

            for symbol in list_active_db_symbols():
                instrument = self.get_instrument_by_symbol(symbol)
                if instrument:
                    instrument_ids_cde.append(instrument.id)
            results.extend(
                self._batch_latest_decisions_for_instances(instance_ids_cde, instrument_ids_cde)
            )

        return results

    def latest_decision_for_instrument(
        self,
        instrument_id: str,
        *,
        strategy_slug: str | None = None,
    ) -> DecisionLogEntry | None:
        stmt = select(OrmDecision).where(OrmDecision.instrument_id == _uuid(instrument_id))
        if strategy_slug:
            instance_rows = self.session.scalars(
                select(OrmStrategyInstance.id)
                .join(OrmStrategyVersion, OrmStrategyInstance.strategy_version_id == OrmStrategyVersion.id)
                .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
                .where(OrmStrategy.slug == strategy_slug)
            ).all()
            if not instance_rows:
                return None
            stmt = stmt.where(
                OrmDecision.strategy_instance_id.in_([_uuid(str(i)) for i in instance_rows])
            )
        row = self.session.scalar(
            stmt.order_by(OrmDecision.candle_timestamp.desc(), OrmDecision.created_at.desc()).limit(1)
        )
        return self._decision_to_domain(row) if row else None

    def resolve_instrument_display_symbols(self) -> dict[str, str]:
        from quantara_engine.market_data.registry import list_target_assets

        mapping: dict[str, str] = {}
        for asset in list_target_assets():
            instrument = self.get_instrument_by_symbol(asset.db_symbol)
            if instrument:
                mapping[instrument.id] = asset.display_symbol
        return mapping

    def count_trades_for_portfolio(self, portfolio_id: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(OrmTrade)
            .where(
                OrmTrade.portfolio_id == _uuid(portfolio_id),
                OrmTrade.backtest_run_id.is_(None),
            )
        ) or 0

    def portfolio_win_rate(self, portfolio_id: str) -> float | None:
        rows = self.session.scalars(
            select(OrmTrade).where(
                OrmTrade.portfolio_id == _uuid(portfolio_id),
                OrmTrade.backtest_run_id.is_(None),
            )
        ).all()
        if not rows:
            return None
        wins = sum(1 for t in rows if t.realized_pnl > 0)
        return wins / len(rows) * 100

    def get_portfolio_risk_slug(self, portfolio_id: str) -> str | None:
        instance = self.get_paper_strategy_instance(portfolio_id)
        if not instance:
            for entry in self.list_competition_entries():
                if entry["portfolio"].id == portfolio_id:
                    return entry["risk_profile"].slug
            return None
        profile = self.get_risk_profile_by_id(instance.risk_profile_id)
        return profile.slug if profile else None

    def get_strategy_by_slug(self, slug: str) -> dict[str, Any] | None:
        row = self.session.scalar(select(OrmStrategy).where(OrmStrategy.slug == slug))
        if not row:
            return None
        return {"id": _str_id(row.id), "slug": row.slug, "name": row.name}

    def get_strategy_version(self, slug: str, version: str) -> dict[str, Any] | None:
        stmt = (
            select(OrmStrategyVersion)
            .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
            .where(OrmStrategy.slug == slug, OrmStrategyVersion.version == version)
        )
        row = self.session.scalar(stmt)
        if not row:
            return None
        return {
            "id": _str_id(row.id),
            "strategy_id": _str_id(row.strategy_id),
            "version": row.version,
            "parameters": row.parameters,
            "logic_hash": row.logic_hash,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def get_or_create_paper_portfolio(
        self,
        owner_id: str,
        name: str = "Backtest Scratch",
        initial_capital: Decimal = Decimal("10000"),
    ) -> Portfolio:
        existing = self.session.scalar(
            select(OrmPortfolio).where(
                OrmPortfolio.owner_id == _uuid(owner_id),
                OrmPortfolio.mode == PortfolioMode.PAPER,
                OrmPortfolio.name == name,
            )
        )
        if existing:
            return self._portfolio_to_domain(existing)

        row = OrmPortfolio(
            id=uuid.uuid4(),
            owner_id=_uuid(owner_id),
            name=name,
            mode=PortfolioMode.PAPER,
            initial_capital=initial_capital,
            balance=initial_capital,
            unrealized_pnl=Decimal("0"),
            equity=initial_capital,
            exposure_notional=Decimal("0"),
            reserved_capital=Decimal("0"),
            currency="USD",
            status=OrmPortfolioStatus.ACTIVE,
            peak_equity=initial_capital,
        )
        self.session.add(row)
        self.session.flush()
        return self._portfolio_to_domain(row)

    def get_paper_strategy_instance(
        self,
        portfolio_id: str,
        strategy_slug: str = "gold-trend-pullback",
        timeframe: str = "1h",
    ) -> StrategyInstance | None:
        stmt = (
            select(OrmStrategyInstance)
            .join(
                OrmStrategyVersion,
                OrmStrategyInstance.strategy_version_id == OrmStrategyVersion.id,
            )
            .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
            .where(
                OrmStrategyInstance.portfolio_id == _uuid(portfolio_id),
                OrmStrategy.slug == strategy_slug,
                OrmStrategyInstance.timeframe == timeframe,
                OrmStrategyInstance.is_active.is_(True),
            )
        )
        row = self.session.scalar(stmt)
        return self._strategy_instance_to_domain(row, strategy_slug) if row else None

    def get_settings_dict(self) -> dict[str, Any]:
        if self._settings_cache is not None:
            return self._settings_cache
        rows = self.session.scalars(select(OrmSetting)).all()
        self._settings_cache = {row.key: row.value for row in rows}
        return self._settings_cache

    def invalidate_settings_cache(self) -> None:
        self._settings_cache = None

    def update_settings(
        self,
        key: str,
        value: Any,
        description: str | None = None,
        *,
        flush: bool = True,
    ) -> None:
        row = self.session.scalar(select(OrmSetting).where(OrmSetting.key == key))
        if row:
            row.value = value
            if description is not None:
                row.description = description
        else:
            self.session.add(
                OrmSetting(id=uuid.uuid4(), key=key, value=value, description=description)
            )
        if flush:
            self.session.flush()
        self.invalidate_settings_cache()

    def save_broker_rejection(
        self,
        *,
        portfolio_id: str,
        symbol: str,
        quantity: Decimal,
        reason: str,
        detail: str = "",
        opportunity_key: str | None = None,
    ) -> None:
        """Persist broker pre-trade rejection for audit (requires migration 0006)."""
        from sqlalchemy import text

        try:
            self.session.execute(
                text(
                    """
                    INSERT INTO broker_order_rejections
                      (strategy_portfolio_id, symbol, quantity, reason, detail, opportunity_key)
                    VALUES (:pid, :sym, :qty, :reason, :detail, :opp)
                    """
                ),
                {
                    "pid": _uuid(portfolio_id),
                    "sym": symbol,
                    "qty": quantity,
                    "reason": reason,
                    "detail": detail[:2000] if detail else None,
                    "opp": opportunity_key,
                },
            )
            self.session.flush()
        except Exception:
            pass  # migration 0006 may not be applied yet

    # ------------------------------------------------------------------ Candles

    def upsert_candle(self, candle: DomainCandle) -> None:
        stmt = (
            insert(OrmCandle)
            .values(
                id=uuid.uuid4(),
                instrument_id=_uuid(candle.instrument_id),
                timeframe=candle.timeframe,
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                source=candle.source,
                is_complete=candle.is_complete,
            )
            .on_conflict_do_update(
                constraint="candles_instrument_timeframe_timestamp_source_unique",
                set_={
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "is_complete": candle.is_complete,
                },
            )
        )
        self.session.execute(stmt)

    def upsert_candles_batch(self, candles: list[DomainCandle]) -> int:
        """Bulk upsert candles in one statement (bootstrap-safe on remote Postgres)."""
        if not candles:
            return 0
        values = [
            {
                "id": uuid.uuid4(),
                "instrument_id": _uuid(candle.instrument_id),
                "timeframe": candle.timeframe,
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "source": candle.source,
                "is_complete": candle.is_complete,
            }
            for candle in candles
        ]
        stmt = insert(OrmCandle).values(values)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="candles_instrument_timeframe_timestamp_source_unique",
            set_={
                "open": excluded.open,
                "high": excluded.high,
                "low": excluded.low,
                "close": excluded.close,
                "volume": excluded.volume,
                "is_complete": excluded.is_complete,
            },
        )
        self.session.execute(stmt)
        return len(candles)

    @staticmethod
    def _candle_ohlcv_columns():
        return (
            OrmCandle.timestamp,
            OrmCandle.open,
            OrmCandle.high,
            OrmCandle.low,
            OrmCandle.close,
            OrmCandle.volume,
            OrmCandle.is_complete,
        )

    def _candle_row_to_domain(
        self,
        row,
        instrument_id: str,
        timeframe: str,
    ) -> DomainCandle:
        return DomainCandle(
            instrument_id=instrument_id,
            timeframe=timeframe,
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            source="db",
            is_complete=row.is_complete,
        )

    def list_candles(
        self,
        instrument_id: str,
        timeframe: str,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> list[DomainCandle]:
        cols = self._candle_ohlcv_columns()
        stmt = (
            select(*cols)
            .where(
                OrmCandle.instrument_id == _uuid(instrument_id),
                OrmCandle.timeframe == timeframe,
            )
            .order_by(OrmCandle.timestamp)
        )
        if since is not None:
            stmt = stmt.where(OrmCandle.timestamp >= since)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = self.session.execute(stmt).all()
        out = [self._candle_row_to_domain(row, instrument_id, timeframe) for row in rows]
        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "list_candles",
                candle_rows=len(out),
                candle_payload_source=out,
            )
        return out

    def list_recent_candles(
        self,
        instrument_id: str,
        timeframe: str,
        limit: int = 50,
    ) -> list[DomainCandle]:
        cols = self._candle_ohlcv_columns()
        stmt = (
            select(*cols)
            .where(
                OrmCandle.instrument_id == _uuid(instrument_id),
                OrmCandle.timeframe == timeframe,
            )
            .order_by(OrmCandle.timestamp.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        out = [
            self._candle_row_to_domain(row, instrument_id, timeframe)
            for row in reversed(rows)
        ]
        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "list_recent_candles",
                candle_rows=len(out),
                candle_payload_source=out,
            )
        return out

    def list_candles_after(
        self,
        instrument_id: str,
        timeframe: str,
        after: datetime,
        *,
        limit: int | None = None,
    ) -> list[DomainCandle]:
        """Incremental tail fetch — candles strictly newer than ``after``."""
        cols = self._candle_ohlcv_columns()
        stmt = (
            select(*cols)
            .where(
                OrmCandle.instrument_id == _uuid(instrument_id),
                OrmCandle.timeframe == timeframe,
                OrmCandle.timestamp > after,
            )
            .order_by(OrmCandle.timestamp)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = self.session.execute(stmt).all()
        out = [self._candle_row_to_domain(row, instrument_id, timeframe) for row in rows]
        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "list_candles_after",
                candle_rows=len(out),
                candle_payload_source=out,
            )
        return out

    def count_candles(self, instrument_id: str, timeframe: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(OrmCandle)
            .where(
                OrmCandle.instrument_id == _uuid(instrument_id),
                OrmCandle.timeframe == timeframe,
            )
        ) or 0

    # ------------------------------------------------------------------ Trading writes

    def save_signal(
        self,
        signal_id: str,
        signal: Signal,
        strategy_instance_id: str,
        strategy_version_id: str,
        instrument_id: str,
        candle_timestamp: datetime,
    ) -> None:
        row = OrmSignal(
            id=_uuid(signal_id),
            strategy_instance_id=_uuid(strategy_instance_id),
            strategy_version_id=_uuid(strategy_version_id),
            instrument_id=_uuid(instrument_id),
            candle_timestamp=candle_timestamp,
            action=OrmSignalAction(signal.action.value),
            reason=signal.reason,
            confidence=signal.confidence,
            suggested_sl=signal.suggested_sl,
            suggested_tp=signal.suggested_tp,
            metadata_=signal.metadata,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        self.session.flush()
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        stamp_paper_run_id(self, table="signals", row_id=signal_id)

    def merge_signal_metadata(self, signal_id: str, patch: dict) -> None:
        """Merge keys into signal.metadata (e.g. risk_audit after approval)."""
        row = self.session.get(OrmSignal, _uuid(signal_id))
        if not row:
            return
        meta = dict(row.metadata_ or {})
        meta.update(patch)
        row.metadata_ = meta

    def list_backtest_entry_signals(self, backtest_run_id: str) -> list[dict]:
        rows = self.session.scalars(
            select(OrmSignal)
            .where(
                OrmSignal.backtest_run_id == _uuid(backtest_run_id),
                OrmSignal.action.in_([OrmSignalAction.BUY, OrmSignalAction.SELL]),
            )
            .order_by(OrmSignal.candle_timestamp.asc())
        ).all()
        return [
            {
                "id": _str_id(row.id),
                "action": row.action.value,
                "candle_timestamp": row.candle_timestamp,
                "metadata": row.metadata_ or {},
            }
            for row in rows
        ]

    def save_decision(self, entry: DecisionLogEntry) -> None:
        row = OrmDecision(
            id=_uuid(entry.id),
            strategy_instance_id=_uuid(entry.strategy_instance_id),
            signal_id=_uuid(entry.signal_id) if entry.signal_id else None,
            instrument_id=_uuid(entry.instrument_id),
            candle_timestamp=entry.candle_timestamp,
            decision_type=OrmDecisionType(entry.decision_type.value),
            message=entry.message,
            metadata_=entry.metadata,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        self.session.flush()
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        stamp_paper_run_id(self, table="decisions", row_id=entry.id)

    def list_consumed_opportunity_keys(self, strategy_instance_id: str) -> list[str]:
        """Opportunity keys consumed for strategy suppression — aligned with opportunity_consumed()."""
        rows = self.session.execute(
            text(
                """
                SELECT DISTINCT s.metadata->>'opportunity_key' AS opp_key
                FROM order_intents oi
                JOIN signals s ON s.id = oi.signal_id
                LEFT JOIN orders o ON o.intent_id = oi.id
                LEFT JOIN fills f ON f.order_id = o.id AND f.side = 'entry'
                WHERE oi.strategy_instance_id = :si
                  AND oi.backtest_run_id IS NULL
                  AND s.metadata->>'opportunity_key' IS NOT NULL
                  AND (
                    oi.status IN ('pending_execution', 'expired', 'rejected')
                    OR (oi.status = 'executed' AND f.id IS NOT NULL)
                  )
                """
            ),
            {"si": _uuid(strategy_instance_id)},
        ).scalars().all()
        return [k for k in rows if k]

    def opportunity_consumed(
        self,
        strategy_instance_id: str,
        opportunity_key: str,
        *,
        include_open: bool = True,
    ) -> bool:
        """True when this canonical opportunity already has a pending or filled entry."""
        from quantara_engine.models.enums import OrderIntentStatus as OrmIntentStatus
        from quantara_engine.risk.opportunity import opportunity_idempotency_key

        idem = opportunity_idempotency_key(strategy_instance_id, opportunity_key)
        row = self.session.scalar(
            select(OrmOrderIntent).where(OrmOrderIntent.idempotency_key == idem).limit(1)
        )
        if not row:
            return False
        if row.status == OrmIntentStatus.PENDING_EXECUTION:
            return True
        if row.status in (OrmIntentStatus.EXPIRED, OrmIntentStatus.REJECTED):
            return True
        if row.status == OrmIntentStatus.EXECUTED:
            fill = self.session.scalar(
                select(OrmFill.id)
                .join(OrmOrder, OrmOrder.id == OrmFill.order_id)
                .where(
                    OrmOrder.intent_id == row.id,
                    OrmFill.side == "entry",
                )
                .limit(1)
            )
            return fill is not None
        return False

    def resolve_entry_opportunity_key(
        self,
        intent: OrderIntent,
        opportunity_key: str | None = None,
    ) -> str | None:
        if opportunity_key:
            return opportunity_key
        row = self.session.get(OrmSignal, _uuid(intent.signal_id))
        if not row:
            return None
        meta = dict(row.metadata_ or {})
        existing = meta.get("opportunity_key")
        if existing:
            return str(existing)
        if row.action not in (OrmSignalAction.BUY, OrmSignalAction.SELL):
            return None
        instance = self.session.get(OrmStrategyInstance, _uuid(intent.strategy_instance_id))
        if not instance:
            return None
        instrument_row = self.session.get(OrmInstrument, _uuid(instance.instrument_id))
        if not instrument_row:
            return None
        from quantara_engine.risk.opportunity import opportunity_key_from_signal

        signal = Signal(
            action=SignalAction(row.action.value),
            reason=row.reason or "",
            suggested_sl=intent.stop_loss,
            suggested_tp=intent.take_profit,
            metadata=meta,
        )
        timeframe = (
            instance.timeframe.value
            if hasattr(instance.timeframe, "value")
            else str(instance.timeframe)
        )
        strategy_slug = self.resolve_strategy_slug(instance)
        if not strategy_slug:
            return None
        return opportunity_key_from_signal(
            signal,
            symbol=instrument_row.symbol,
            timeframe=timeframe,
            strategy_slug=strategy_slug,
            setup_candle_timestamp=intent.signal_candle_timestamp,
        )

    def _requires_canonical_opportunity_key(self, intent: OrderIntent) -> bool:
        if self._bt_uuid() is not None:
            return False
        if intent.is_close or intent.take_profit is None:
            return False
        from quantara_engine.competition.leverage import is_paper_competition_portfolio
        from quantara_engine.competition.orb_constants import ORB_STRATEGY_SLUG

        if not is_paper_competition_portfolio(intent.portfolio_id):
            return False
        instance = self.session.get(OrmStrategyInstance, _uuid(intent.strategy_instance_id))
        if not instance:
            return False
        slug = self.resolve_strategy_slug(instance)
        return slug in (ORB_STRATEGY_SLUG, "gold-trend-pullback")

    def save_order_intent(
        self, intent: OrderIntent, *, opportunity_key: str | None = None
    ) -> OrderIntent:
        resolved_key = self.resolve_entry_opportunity_key(intent, opportunity_key)
        if self._requires_canonical_opportunity_key(intent) and not resolved_key:
            raise ValueError(
                "Competition entry intent requires canonical opportunity_key "
                f"(instance={intent.strategy_instance_id})"
            )
        idempotency_key = _intent_idempotency_key(
            intent.strategy_instance_id,
            intent.signal_candle_timestamp,
            intent.direction.value,
            opportunity_key=resolved_key,
        )
        existing = self.session.scalar(
            select(OrmOrderIntent).where(OrmOrderIntent.idempotency_key == idempotency_key)
        )
        if existing:
            return self._order_intent_to_domain(existing)

        row = OrmOrderIntent(
            id=_uuid(intent.id),
            signal_id=_uuid(intent.signal_id),
            strategy_instance_id=_uuid(intent.strategy_instance_id),
            portfolio_id=_uuid(intent.portfolio_id),
            direction=OrmDirection(intent.direction.value),
            quantity=intent.quantity,
            entry_type=EntryType.LIMIT if intent.limit_price else EntryType.MARKET,
            limit_price=intent.limit_price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            target_risk_amount=intent.target_risk_amount,
            actual_risk_amount=intent.actual_risk_amount,
            signal_candle_timestamp=intent.signal_candle_timestamp,
            execution_candle_timestamp=intent.execution_candle_timestamp,
            risk_profile_id=_uuid(intent.risk_profile_id),
            status=OrderIntentStatus(intent.status.value),
            idempotency_key=idempotency_key,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        self.session.flush()
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        stamp_paper_run_id(self, table="order_intents", row_id=intent.id)
        return intent

    def update_order_intent_status(
        self,
        intent_id: str,
        status: IntentStatus,
        rejection_reason: str | None = None,
    ) -> None:
        row = self.session.get(OrmOrderIntent, _uuid(intent_id))
        if not row:
            return
        row.status = OrderIntentStatus(status.value)
        if rejection_reason:
            row.rejection_reason = rejection_reason
        self.session.flush()

    def list_pending_order_intents(
        self,
        portfolio_id: str,
        strategy_instance_id: str,
    ) -> list[OrderIntent]:
        from quantara_engine.competition.paper_run import intent_scope_clause

        rows = self.session.scalars(
            select(OrmOrderIntent)
            .where(
                OrmOrderIntent.portfolio_id == _uuid(portfolio_id),
                OrmOrderIntent.strategy_instance_id == _uuid(strategy_instance_id),
                OrmOrderIntent.status == OrderIntentStatus.PENDING_EXECUTION,
                intent_scope_clause(self),
            )
            .order_by(OrmOrderIntent.execution_candle_timestamp)
        ).all()
        return [self._order_intent_to_domain(row) for row in rows]

    def find_pending_intent_for_signal_candle(
        self,
        strategy_instance_id: str,
        signal_candle_timestamp: datetime,
        direction: str | None = None,
    ) -> OrderIntent | None:
        stmt = select(OrmOrderIntent).where(
            OrmOrderIntent.strategy_instance_id == _uuid(strategy_instance_id),
            OrmOrderIntent.signal_candle_timestamp == signal_candle_timestamp,
            OrmOrderIntent.status == OrderIntentStatus.PENDING_EXECUTION,
            OrmOrderIntent.backtest_run_id.is_(None),
        )
        if direction is not None:
            from quantara_engine.models.enums import Direction as OrmDirection

            stmt = stmt.where(OrmOrderIntent.direction == OrmDirection(direction))
        row = self.session.scalar(stmt.limit(1))
        return self._order_intent_to_domain(row) if row else None

    def has_duplicate_entry_for_signal(
        self,
        strategy_instance_id: str,
        signal_candle_timestamp: datetime,
        signal_action: str,
    ) -> bool:
        """True when the exact same signal candle+direction already has a pending or filled entry."""
        from quantara_engine.models.enums import Direction as OrmDirection

        direction = (
            OrmDirection.LONG if signal_action == "buy" else OrmDirection.SHORT
        )
        pending = self.find_pending_intent_for_signal_candle(
            strategy_instance_id,
            signal_candle_timestamp,
            direction.value,
        )
        if pending:
            return True
        fill_row = self.session.scalar(
            select(OrmFill.id)
            .join(OrmOrder, OrmOrder.id == OrmFill.order_id)
            .join(OrmOrderIntent, OrmOrder.intent_id == OrmOrderIntent.id)
            .where(
                OrmOrderIntent.strategy_instance_id == _uuid(strategy_instance_id),
                OrmOrderIntent.signal_candle_timestamp == signal_candle_timestamp,
                OrmOrderIntent.direction == direction,
                OrmFill.side == "entry",
                OrmOrderIntent.backtest_run_id.is_(None),
            )
            .limit(1)
        )
        return fill_row is not None

    def save_order(
        self,
        order: Order,
        strategy_instance_id: str,
        signal_id: str | None = None,
        *,
        flush: bool = True,
    ) -> None:
        intent_id = order.intent_id
        if not intent_id:
            intent_id = self._ensure_exit_intent(order, strategy_instance_id, signal_id)
            order.intent_id = intent_id

        row = OrmOrder(
            id=_uuid(order.id),
            intent_id=_uuid(intent_id),
            portfolio_id=_uuid(order.portfolio_id),
            instrument_id=_uuid(order.instrument_id),
            direction=OrmDirection(order.direction.value),
            quantity=order.quantity,
            order_type=OrderType.MARKET,
            status=OrmOrderStatus(order.status.value),
            broker_order_id=order.broker_order_id,
            submitted_at=order.submitted_at,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        if flush:
            self.session.flush()

    def save_fill(
        self,
        fill_id: str,
        order_id: str,
        fill: FillResult | Fill,
        side: str,
        filled_at: datetime,
        quantity: Decimal,
        position_id: str | None = None,
    ) -> None:
        if isinstance(fill, FillResult):
            row = OrmFill(
                id=_uuid(fill_id),
                order_id=_uuid(order_id),
                position_id=_uuid(position_id) if position_id else None,
                fill_price=fill.fill_price,
                fill_quantity=quantity,
                fees=fill.fees,
                slippage=fill.slippage,
                spread_cost=fill.spread_cost,
                base_price=fill.base_price,
                side=FillSide(side),
                filled_at=filled_at,
                mode=_mode_to_orm(self.mode),
                backtest_run_id=self._bt_uuid(),
            )
        else:
            row = OrmFill(
                id=_uuid(fill_id),
                order_id=_uuid(order_id),
                position_id=_uuid(position_id) if position_id else None,
                fill_price=fill.fill_price,
                fill_quantity=fill.fill_quantity,
                fees=fill.fees,
                slippage=fill.slippage,
                spread_cost=fill.spread_cost,
                base_price=fill.base_price,
                side=FillSide(side),
                filled_at=filled_at or fill.filled_at or datetime.now(timezone.utc),
                mode=_mode_to_orm(self.mode),
                backtest_run_id=self._bt_uuid(),
            )
        self.session.merge(row)

    def save_position(self, position: Position, *, intent_id: str | None = None) -> None:
        from quantara_engine.competition.paper_run import (
            paper_run_columns_ready,
            resolve_position_paper_run_id,
        )

        paper_run_id = resolve_position_paper_run_id(self, intent_id=intent_id)
        row = OrmPosition(
            id=_uuid(position.id),
            portfolio_id=_uuid(position.portfolio_id),
            strategy_instance_id=_uuid(position.strategy_instance_id),
            instrument_id=_uuid(position.instrument_id),
            direction=OrmDirection(position.direction.value),
            quantity=position.quantity,
            entry_price=position.entry_price,
            current_price=position.current_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            unrealized_pnl=position.unrealized_pnl,
            status=OrmPositionStatus(position.status.value),
            opened_at=position.opened_at or datetime.now(timezone.utc),
            closed_at=position.closed_at,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
            paper_run_id=_uuid(paper_run_id) if paper_run_id and paper_run_columns_ready(self) else None,
        )
        self.session.merge(row)
        self.session.flush()

    def update_position_closed(
        self,
        position_id: str,
        closed_at: datetime,
        current_price: Decimal | None = None,
        *,
        flush: bool = True,
    ) -> None:
        row = self.session.get(OrmPosition, _uuid(position_id))
        if not row:
            return
        row.status = OrmPositionStatus.CLOSED
        row.closed_at = closed_at
        if current_price is not None:
            row.current_price = current_price
        row.unrealized_pnl = Decimal("0")
        if flush:
            self.session.flush()

    def update_positions_closed_batch(
        self,
        closes: list[tuple[str, datetime, Decimal | None]],
        *,
        chunk_size: int = 10,
    ) -> None:
        if not closes:
            return
        closes = sorted(closes, key=lambda row: row[0])
        for offset in range(0, len(closes), chunk_size):
            chunk = closes[offset : offset + chunk_size]
            ids = [_uuid(position_id) for position_id, _, _ in chunk]
            rows = {
                _str_id(row.id): row
                for row in self.session.scalars(
                    select(OrmPosition).where(OrmPosition.id.in_(ids))
                ).all()
            }
            for position_id, closed_at, current_price in chunk:
                row = rows.get(position_id)
                if not row:
                    continue
                row.status = OrmPositionStatus.CLOSED
                row.closed_at = closed_at
                if current_price is not None:
                    row.current_price = current_price
                row.unrealized_pnl = Decimal("0")
            self.session.flush()

    def update_open_position_mark(
        self,
        position_id: str,
        mark_price: Decimal,
        unrealized_pnl: Decimal,
        *,
        flush: bool = True,
    ) -> None:
        row = self.session.get(OrmPosition, _uuid(position_id))
        if not row:
            return
        row.current_price = mark_price
        row.unrealized_pnl = unrealized_pnl
        if flush:
            self.session.flush()

    def resolve_strategy_version_id(self, strategy_instance_id: str) -> str:
        row = self.session.get(OrmStrategyInstance, _uuid(strategy_instance_id))
        if not row:
            raise ValueError(f"Strategy instance not found: {strategy_instance_id}")
        return _str_id(row.strategy_version_id)

    def _hydrate_position_strategy_versions(self, positions: list[Position]) -> None:
        if not positions:
            return
        instance_ids = list({position.strategy_instance_id for position in positions})
        rows = self.session.execute(
            select(OrmStrategyInstance.id, OrmStrategyInstance.strategy_version_id).where(
                OrmStrategyInstance.id.in_([_uuid(instance_id) for instance_id in instance_ids])
            )
        ).all()
        version_by_instance = {_str_id(row[0]): _str_id(row[1]) for row in rows}
        for position in positions:
            if not _is_uuid(position.strategy_version_id):
                resolved = version_by_instance.get(position.strategy_instance_id)
                if resolved:
                    position.strategy_version_id = resolved

    def _lookup_entry_risk_for_position(
        self, position_id: str
    ) -> tuple[Decimal, Decimal] | None:
        row = self.session.execute(
            text(
                """
                SELECT oi.target_risk_amount, oi.actual_risk_amount
                FROM fills f
                JOIN orders o ON o.id = f.order_id
                JOIN order_intents oi ON oi.id = o.intent_id
                WHERE f.position_id = :pid AND f.side = 'entry'
                ORDER BY f.filled_at ASC
                LIMIT 1
                """
            ),
            {"pid": _uuid(position_id)},
        ).first()
        if not row:
            return None
        return Decimal(str(row[0])), Decimal(str(row[1]))

    def save_trade(self, trade: Trade) -> None:
        strategy_version_id = trade.strategy_version_id
        if not _is_uuid(strategy_version_id):
            strategy_version_id = self.resolve_strategy_version_id(trade.strategy_instance_id)
        target_risk = trade.target_risk_amount
        actual_risk = trade.actual_risk_amount
        if target_risk <= 0 or actual_risk <= 0:
            looked_up = self._lookup_entry_risk_for_position(trade.position_id)
            if looked_up:
                target_risk = target_risk if target_risk > 0 else looked_up[0]
                actual_risk = actual_risk if actual_risk > 0 else looked_up[1]
        row = OrmTrade(
            id=_uuid(trade.id),
            position_id=_uuid(trade.position_id),
            portfolio_id=_uuid(trade.portfolio_id),
            strategy_instance_id=_uuid(trade.strategy_instance_id),
            strategy_version_id=_uuid(strategy_version_id),
            instrument_id=_uuid(trade.instrument_id),
            direction=OrmDirection(trade.direction.value),
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            gross_pnl=trade.gross_pnl,
            realized_pnl=trade.realized_pnl,
            fees_total=trade.fees_total,
            slippage_total=trade.slippage_total,
            spread_total=trade.spread_total,
            target_risk_amount=target_risk,
            actual_risk_amount=actual_risk,
            exit_reason=OrmExitReason(trade.exit_reason.value),
            duration_seconds=trade.duration_seconds,
            opened_at=trade.opened_at,
            closed_at=trade.closed_at,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        stamp_paper_run_id(self, table="trades", row_id=trade.id)

    def hydrate_position_risk_from_intents(self, positions: list[Position]) -> None:
        """Fill in-memory risk amounts from entry order intents when missing."""
        if not positions:
            return
        for pos in positions:
            if pos.target_risk_amount > 0 and pos.actual_risk_amount > 0:
                continue
            looked_up = self._lookup_entry_risk_for_position(pos.id)
            if looked_up:
                pos.target_risk_amount = looked_up[0]
                pos.actual_risk_amount = looked_up[1]

    def update_portfolio(self, portfolio: Portfolio, *, flush: bool = True) -> None:
        row = self.session.get(OrmPortfolio, _uuid(portfolio.id))
        if not row:
            return
        row.balance = portfolio.balance
        row.unrealized_pnl = portfolio.unrealized_pnl
        row.equity = portfolio.equity
        row.exposure_notional = portfolio.exposure_notional
        row.reserved_capital = portfolio.reserved_capital
        row.status = OrmPortfolioStatus(portfolio.status.value)
        row.halt_reason = portfolio.halt_reason
        row.peak_equity = portfolio.peak_equity
        if flush:
            self.session.flush()

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> str:
        snap_id = str(uuid.uuid4())
        row = OrmPortfolioSnapshot(
            id=_uuid(snap_id),
            portfolio_id=_uuid(snapshot.portfolio_id),
            timestamp=snapshot.timestamp,
            balance=snapshot.balance,
            equity=snapshot.equity,
            exposure_notional=snapshot.exposure_notional,
            reserved_capital=snapshot.reserved_capital,
            unrealized_pnl=snapshot.unrealized_pnl,
            drawdown_pct=snapshot.drawdown_pct,
            open_positions_count=snapshot.open_positions_count,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.add(row)
        self.session.flush()
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        stamp_paper_run_id(self, table="portfolio_snapshots", row_id=snap_id)
        return snap_id

    def save_snapshots_batch(self, snapshots: list[PortfolioSnapshot]) -> None:
        from quantara_engine.competition.paper_run import stamp_paper_run_id

        for snapshot in snapshots:
            snap_id = uuid.uuid4()
            self.session.add(
                OrmPortfolioSnapshot(
                    id=snap_id,
                    portfolio_id=_uuid(snapshot.portfolio_id),
                    timestamp=snapshot.timestamp,
                    balance=snapshot.balance,
                    equity=snapshot.equity,
                    exposure_notional=snapshot.exposure_notional,
                    reserved_capital=snapshot.reserved_capital,
                    unrealized_pnl=snapshot.unrealized_pnl,
                    drawdown_pct=snapshot.drawdown_pct,
                    open_positions_count=snapshot.open_positions_count,
                    mode=_mode_to_orm(self.mode),
                    backtest_run_id=self._bt_uuid(),
                )
            )
            stamp_paper_run_id(self, table="portfolio_snapshots", row_id=str(snap_id))

    def update_portfolios_batch(
        self,
        portfolios: list[Portfolio],
        *,
        chunk_size: int = 15,
    ) -> None:
        if not portfolios:
            return
        portfolios = sorted(portfolios, key=lambda p: p.id)
        for offset in range(0, len(portfolios), chunk_size):
            chunk = portfolios[offset : offset + chunk_size]
            ids = [_uuid(p.id) for p in chunk]
            rows = {
                _str_id(row.id): row
                for row in self.session.scalars(
                    select(OrmPortfolio).where(OrmPortfolio.id.in_(ids))
                ).all()
            }
            for portfolio in chunk:
                row = rows.get(portfolio.id)
                if not row:
                    continue
                row.balance = portfolio.balance
                row.unrealized_pnl = portfolio.unrealized_pnl
                row.equity = portfolio.equity
                row.exposure_notional = portfolio.exposure_notional
                row.reserved_capital = portfolio.reserved_capital
                row.status = OrmPortfolioStatus(portfolio.status.value)
                row.halt_reason = portfolio.halt_reason
                row.peak_equity = portfolio.peak_equity
            self.session.flush()

    def update_portfolios_equity_snapshot_batch(
        self,
        portfolios: list[Portfolio],
        *,
        chunk_size: int = 15,
    ) -> None:
        """Mark-to-market snapshot write — never overwrite balance (realized ledger)."""
        if not portfolios:
            return
        portfolios = sorted(portfolios, key=lambda p: p.id)
        for offset in range(0, len(portfolios), chunk_size):
            chunk = portfolios[offset : offset + chunk_size]
            ids = [_uuid(p.id) for p in chunk]
            rows = {
                _str_id(row.id): row
                for row in self.session.scalars(
                    select(OrmPortfolio).where(OrmPortfolio.id.in_(ids))
                ).all()
            }
            for portfolio in chunk:
                row = rows.get(portfolio.id)
                if not row:
                    continue
                row.unrealized_pnl = portfolio.unrealized_pnl
                row.equity = (row.balance + portfolio.unrealized_pnl).quantize(Decimal("0.01"))
                row.exposure_notional = portfolio.exposure_notional
                row.reserved_capital = portfolio.reserved_capital
                if row.equity > row.peak_equity:
                    row.peak_equity = row.equity
            self.session.flush()

    def trade_exists_for_position(self, position_id: str) -> bool:
        row = self.session.scalar(
            select(OrmTrade.id).where(OrmTrade.position_id == _uuid(position_id)).limit(1)
        )
        return row is not None

    def sum_realized_pnl_for_portfolio_ids(self, portfolio_ids: list[str]) -> dict[str, Decimal]:
        if not portfolio_ids:
            return {}
        ids = [_uuid(pid) for pid in portfolio_ids]
        from quantara_engine.competition.paper_run import trade_scope_clause

        rows = self.session.execute(
            select(
                OrmTrade.portfolio_id,
                func.coalesce(func.sum(OrmTrade.realized_pnl), 0),
            )
            .where(
                OrmTrade.portfolio_id.in_(ids),
                trade_scope_clause(self),
            )
            .group_by(OrmTrade.portfolio_id)
        ).all()
        return {_str_id(row[0]): Decimal(str(row[1])) for row in rows}

    def sum_open_position_financials_for_portfolio_ids(
        self,
        portfolio_ids: list[str],
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """Return {portfolio_id: (unrealized_pnl_sum, exposure_notional_sum)} for OPEN positions."""
        from quantara_engine.competition.paper_run import position_scope_clause

        if not portfolio_ids:
            return {}
        ids = [_uuid(pid) for pid in portfolio_ids]
        rows = self.session.execute(
            select(
                OrmPosition.portfolio_id,
                func.coalesce(func.sum(OrmPosition.unrealized_pnl), 0),
                func.coalesce(
                    func.sum(OrmPosition.quantity * OrmPosition.current_price),
                    0,
                ),
            )
            .where(
                OrmPosition.portfolio_id.in_(ids),
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
            .group_by(OrmPosition.portfolio_id)
        ).all()
        return {
            _str_id(row[0]): (Decimal(str(row[1])), Decimal(str(row[2])))
            for row in rows
        }

    def update_portfolios_financial_canonical_batch(
        self,
        portfolios: list[Portfolio],
        *,
        chunk_size: int = 15,
    ) -> None:
        """Write canonical balance/unrealized/equity/exposure — sole owner after ledger sync."""
        if not portfolios:
            return
        portfolios = sorted(portfolios, key=lambda p: p.id)
        for offset in range(0, len(portfolios), chunk_size):
            chunk = portfolios[offset : offset + chunk_size]
            ids = [_uuid(p.id) for p in chunk]
            rows = {
                _str_id(row.id): row
                for row in self.session.scalars(
                    select(OrmPortfolio).where(OrmPortfolio.id.in_(ids))
                ).all()
            }
            for portfolio in chunk:
                row = rows.get(portfolio.id)
                if not row:
                    continue
                row.balance = portfolio.balance
                row.unrealized_pnl = portfolio.unrealized_pnl
                row.equity = portfolio.equity
                row.exposure_notional = portfolio.exposure_notional
                row.reserved_capital = portfolio.reserved_capital
                if portfolio.peak_equity > row.peak_equity:
                    row.peak_equity = portfolio.peak_equity
            self.session.flush()

    def update_portfolio_status_only(
        self,
        portfolio: Portfolio,
        *,
        flush: bool = True,
    ) -> None:
        """Update non-financial portfolio fields only (halt/resume)."""
        row = self.session.get(OrmPortfolio, _uuid(portfolio.id))
        if not row:
            return
        row.status = OrmPortfolioStatus(portfolio.status.value)
        row.halt_reason = portfolio.halt_reason
        if flush:
            self.session.flush()

    def sync_portfolios_financial_state_from_ledger(
        self,
        portfolios: list[Portfolio],
        *,
        flush: bool = False,
    ) -> None:
        """
        Canonical financial sync after exit persistence:
        flush → SUM(realized) → SUM(open position unrealized) → balance/equity → persist.
        """
        if not portfolios:
            return
        from quantara_engine.portfolio.balance_reconciliation import apply_canonical_financial_state

        self.session.flush()
        pids = [p.id for p in portfolios]
        realized_map = self.sum_realized_pnl_for_portfolio_ids(pids)
        open_fin = self.sum_open_position_financials_for_portfolio_ids(pids)
        for portfolio in portfolios:
            unreal, exposure = open_fin.get(portfolio.id, (Decimal("0"), Decimal("0")))
            apply_canonical_financial_state(
                portfolio,
                realized_pnl_sum=realized_map.get(portfolio.id, Decimal("0")),
                unrealized_pnl_sum=unreal,
                exposure_notional=exposure,
            )
        self.update_portfolios_financial_canonical_batch(portfolios)
        if flush:
            self.session.flush()

    def sync_portfolios_balance_from_ledger(
        self,
        portfolios: list[Portfolio],
        *,
        flush: bool = False,
    ) -> None:
        """Backward-compatible alias — full canonical financial sync."""
        self.sync_portfolios_financial_state_from_ledger(portfolios, flush=flush)

    def update_open_position_marks_batch(
        self,
        updates: list[tuple[str, Decimal, Decimal]],
        *,
        chunk_size: int = 10,
    ) -> None:
        if not updates:
            return
        updates = sorted(updates, key=lambda row: row[0])
        for offset in range(0, len(updates), chunk_size):
            chunk = updates[offset : offset + chunk_size]
            ids = [_uuid(position_id) for position_id, _, _ in chunk]
            rows = {
                _str_id(row.id): row
                for row in self.session.scalars(
                    select(OrmPosition).where(OrmPosition.id.in_(ids))
                ).all()
            }
            for position_id, mark_price, unrealized_pnl in chunk:
                row = rows.get(position_id)
                if not row:
                    continue
                row.current_price = mark_price
                row.unrealized_pnl = unrealized_pnl
            self.session.flush()

    # ------------------------------------------------------------------ Load

    def load_portfolio_state(self, portfolio_id: str) -> PortfolioState:
        from quantara_engine.competition.paper_run import (
            position_scope_clause,
            snapshot_scope_clause,
            trade_scope_clause,
        )

        portfolio_row = self.session.get(OrmPortfolio, _uuid(portfolio_id))
        if not portfolio_row:
            raise ValueError(f"Portfolio not found: {portfolio_id}")

        portfolio = self._portfolio_to_domain(portfolio_row)

        position_rows = self.session.scalars(
            select(OrmPosition).where(
                OrmPosition.portfolio_id == _uuid(portfolio_id),
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
        ).all()
        positions = [self._position_to_domain(row) for row in position_rows]
        self._hydrate_position_strategy_versions(positions)
        self.hydrate_position_risk_from_intents(positions)

        trade_rows = self.session.scalars(
            select(OrmTrade)
            .where(
                OrmTrade.portfolio_id == _uuid(portfolio_id),
                trade_scope_clause(self),
            )
            .order_by(OrmTrade.closed_at)
        ).all()
        trades = [self._trade_to_domain(row) for row in trade_rows]

        snapshot_rows = self.session.scalars(
            select(OrmPortfolioSnapshot)
            .where(
                OrmPortfolioSnapshot.portfolio_id == _uuid(portfolio_id),
                snapshot_scope_clause(self),
            )
            .order_by(OrmPortfolioSnapshot.timestamp)
        ).all()
        snapshots = [self._snapshot_to_domain(row) for row in snapshot_rows]

        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "load_portfolio_state",
                trade_rows=len(trades),
                snapshot_rows=len(snapshots),
            )

        return PortfolioState(
            portfolio=portfolio,
            positions=positions,
            trades=trades,
            snapshots=snapshots,
        )

    def load_portfolio_for_snapshot(self, portfolio_id: str) -> PortfolioState:
        """Lightweight state for snapshot job — open positions only, no history."""
        return self.load_portfolio_runtime_state(portfolio_id)

    def load_portfolio_runtime_state(self, portfolio_id: str) -> PortfolioState:
        """Live runtime state — open positions only, no trade/snapshot history."""
        from quantara_engine.competition.paper_run import position_scope_clause

        portfolio_row = self.session.get(OrmPortfolio, _uuid(portfolio_id))
        if not portfolio_row:
            raise ValueError(f"Portfolio not found: {portfolio_id}")

        portfolio = self._portfolio_to_domain(portfolio_row)
        position_rows = self.session.scalars(
            select(OrmPosition).where(
                OrmPosition.portfolio_id == _uuid(portfolio_id),
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
        ).all()
        positions = [self._position_to_domain(row) for row in position_rows]
        self._hydrate_position_strategy_versions(positions)
        self.hydrate_position_risk_from_intents(positions)
        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "load_portfolio_runtime_state",
                position_rows=len(positions),
                portfolio_rows=1,
            )
        return PortfolioState(
            portfolio=portfolio,
            positions=positions,
            trades=[],
            snapshots=[],
        )

    def batch_load_portfolio_states(self, portfolio_ids: list[str]) -> dict[str, PortfolioState]:
        """Load open-position portfolio states for PM — two queries total."""
        from quantara_engine.competition.paper_run import position_scope_clause

        if not portfolio_ids:
            return {}
        unique_ids = sorted(set(portfolio_ids))
        uuids = [_uuid(pid) for pid in unique_ids]

        portfolio_rows = self.session.scalars(
            select(OrmPortfolio).where(OrmPortfolio.id.in_(uuids))
        ).all()
        portfolios = { _str_id(row.id): self._portfolio_to_domain(row) for row in portfolio_rows }

        position_rows = self.session.scalars(
            select(OrmPosition).where(
                OrmPosition.portfolio_id.in_(uuids),
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
        ).all()
        positions_by_portfolio: dict[str, list[Position]] = {pid: [] for pid in unique_ids}
        all_positions: list[Position] = []
        for row in position_rows:
            pos = self._position_to_domain(row)
            all_positions.append(pos)
            positions_by_portfolio.setdefault(_str_id(row.portfolio_id), []).append(pos)
        self._hydrate_position_strategy_versions(all_positions)
        self.hydrate_position_risk_from_intents(all_positions)

        states = {
            pid: PortfolioState(
                portfolio=portfolios[pid],
                positions=positions_by_portfolio.get(pid, []),
                trades=[],
                snapshots=[],
            )
            for pid in unique_ids
            if pid in portfolios
        }
        if self.egress_metrics is not None:
            self.egress_metrics.note_query(
                "batch_load_portfolio_states",
                position_rows=len(all_positions),
                portfolio_rows=len(portfolios),
            )
        return states

    # ------------------------------------------------------------------ Backtest

    def create_backtest_run_record(
        self,
        run_id: str,
        portfolio_id: str,
        strategy_instance_id: str,
        strategy_version_id: str,
        instrument_id: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal,
        parameters: dict[str, Any],
        execution_assumptions: dict[str, Any],
        dataset_fingerprint: str,
    ) -> None:
        row = OrmBacktestRun(
            id=_uuid(run_id),
            portfolio_id=_uuid(portfolio_id),
            strategy_instance_id=_uuid(strategy_instance_id),
            strategy_version_id=_uuid(strategy_version_id),
            instrument_id=_uuid(instrument_id),
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            status=BacktestStatus.RUNNING,
            parameters=parameters,
            execution_assumptions=execution_assumptions,
            dataset_fingerprint=dataset_fingerprint,
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()

    def update_backtest_run_completed(
        self,
        run_id: str,
        final_capital: Decimal,
        metrics: dict[str, Any],
        status: str = "completed",
        error_message: str | None = None,
    ) -> None:
        row = self.session.get(OrmBacktestRun, _uuid(run_id))
        if not row:
            return
        row.final_capital = final_capital
        row.metrics = metrics
        row.status = BacktestStatus(status)
        row.completed_at = datetime.now(timezone.utc)
        row.error_message = error_message
        self.session.flush()

    def list_backtests(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(OrmBacktestRun).order_by(OrmBacktestRun.created_at.desc()).limit(limit)
        ).all()
        return [self._backtest_to_dict(row) for row in rows]

    def get_backtest(self, run_id: str) -> dict[str, Any] | None:
        row = self.session.get(OrmBacktestRun, _uuid(run_id))
        return self._backtest_to_dict(row) if row else None

    # ------------------------------------------------------------------ API queries

    def list_decisions(
        self,
        strategy_instance_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionLogEntry]:
        stmt = select(OrmDecision).order_by(OrmDecision.created_at.desc())
        if strategy_instance_id:
            stmt = stmt.where(
                OrmDecision.strategy_instance_id == _uuid(strategy_instance_id)
            )
        stmt = stmt.offset(offset).limit(limit)
        rows = self.session.scalars(stmt).all()
        return [self._decision_to_domain(row) for row in rows]

    def list_trades(
        self,
        portfolio_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        paper_only: bool = False,
    ) -> list[Trade]:
        stmt = select(OrmTrade).order_by(OrmTrade.closed_at.desc())
        if portfolio_id:
            stmt = stmt.where(OrmTrade.portfolio_id == _uuid(portfolio_id))
        if paper_only:
            stmt = stmt.where(OrmTrade.backtest_run_id.is_(None))
        stmt = stmt.offset(offset).limit(limit)
        rows = self.session.scalars(stmt).all()
        return [self._trade_to_domain(row) for row in rows]

    def list_positions(
        self,
        portfolio_id: str,
        open_only: bool = True,
        status: str | None = None,
    ) -> list[Position]:
        from quantara_engine.competition.paper_run import position_scope_clause

        stmt = select(OrmPosition).where(OrmPosition.portfolio_id == _uuid(portfolio_id))
        if status:
            stmt = stmt.where(OrmPosition.status == OrmPositionStatus(status))
        elif open_only:
            stmt = stmt.where(
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
        rows = self.session.scalars(stmt).all()
        return [self._position_to_domain(row) for row in rows]

    def list_snapshots(
        self,
        portfolio_id: str,
        limit: int = 100,
    ) -> list[PortfolioSnapshot]:
        from quantara_engine.competition.paper_run import snapshot_scope_clause

        rows = self.session.scalars(
            select(OrmPortfolioSnapshot)
            .where(
                OrmPortfolioSnapshot.portfolio_id == _uuid(portfolio_id),
                snapshot_scope_clause(self),
            )
            .order_by(OrmPortfolioSnapshot.timestamp.desc())
            .limit(limit)
        ).all()
        return [self._snapshot_to_domain(row) for row in rows]

    def get_start_of_day_equity(
        self,
        portfolio_id: str,
        day_start: datetime,
    ) -> Decimal | None:
        row = self.session.scalar(
            select(OrmPortfolioSnapshot.equity)
            .where(
                OrmPortfolioSnapshot.portfolio_id == _uuid(portfolio_id),
                OrmPortfolioSnapshot.timestamp >= day_start,
                OrmPortfolioSnapshot.backtest_run_id.is_(None),
                OrmPortfolioSnapshot.mode == PortfolioMode.PAPER,
            )
            .order_by(OrmPortfolioSnapshot.timestamp.asc())
            .limit(1)
        )
        return Decimal(str(row)) if row is not None else None

    def count_decisions_today(
        self,
        strategy_instance_id: str | None = None,
        mode: Mode | None = None,
    ) -> int:
        """Count decisions since UTC midnight; optional instance + mode filters."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(func.count()).select_from(OrmDecision).where(
            OrmDecision.created_at >= today_start
        )
        if strategy_instance_id:
            stmt = stmt.where(
                OrmDecision.strategy_instance_id == _uuid(strategy_instance_id)
            )
        if mode is not None:
            stmt = stmt.where(OrmDecision.mode == _mode_to_orm(mode))
        return self.session.scalar(stmt) or 0

    def count_trades_today(
        self,
        portfolio_id: str | None = None,
        *,
        paper_only: bool = True,
    ) -> int:
        """Count trades closed since UTC midnight for a portfolio (paper by default)."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(func.count()).select_from(OrmTrade).where(
            OrmTrade.closed_at >= today_start
        )
        if portfolio_id:
            stmt = stmt.where(OrmTrade.portfolio_id == _uuid(portfolio_id))
        if paper_only:
            stmt = stmt.where(OrmTrade.backtest_run_id.is_(None))
        return self.session.scalar(stmt) or 0

    def has_decision_for_candle(
        self,
        strategy_instance_id: str,
        candle_timestamp: datetime,
    ) -> bool:
        row = self.session.scalar(
            select(OrmDecision.id)
            .where(
                OrmDecision.strategy_instance_id == _uuid(strategy_instance_id),
                OrmDecision.candle_timestamp == candle_timestamp,
            )
            .limit(1)
        )
        return row is not None

    def timeframe_group_already_processed(
        self,
        instance_ids: list[str],
        instrument_id: str,
        candle_timestamp: datetime,
    ) -> bool:
        if not instance_ids:
            return True
        count = self.session.scalar(
            select(func.count())
            .select_from(OrmDecision)
            .where(
                OrmDecision.strategy_instance_id.in_([_uuid(i) for i in instance_ids]),
                OrmDecision.instrument_id == _uuid(instrument_id),
                OrmDecision.candle_timestamp == candle_timestamp,
            )
        ) or 0
        return count >= len(instance_ids)

    def get_timeframe_group_last_processed(
        self,
        instance_ids: list[str],
        instrument_id: str,
    ) -> datetime | None:
        """Latest candle timestamp fully processed for this instrument by all instances."""
        processed = self.fully_processed_candle_timestamps(instance_ids, instrument_id)
        return max(processed) if processed else None

    def fully_processed_candle_timestamps(
        self,
        instance_ids: list[str],
        instrument_id: str,
        *,
        since: datetime | None = None,
    ) -> set[datetime]:
        """All candle timestamps where every instance in the group has a decision."""
        if not instance_ids:
            return set()
        inst_uuids = [_uuid(i) for i in instance_ids]
        stmt = (
            select(OrmDecision.candle_timestamp, func.count())
            .where(
                OrmDecision.strategy_instance_id.in_(inst_uuids),
                OrmDecision.instrument_id == _uuid(instrument_id),
            )
            .group_by(OrmDecision.candle_timestamp)
            .having(func.count() >= len(instance_ids))
        )
        if since is not None:
            stmt = stmt.where(OrmDecision.candle_timestamp >= since)
        rows = self.session.execute(stmt).all()
        out = {row[0] for row in rows}
        if self.egress_metrics is not None:
            self.egress_metrics.note_query("fully_processed_candle_timestamps")
        return out

    def list_decision_timestamps_for_group(
        self,
        instance_ids: list[str],
    ) -> list[datetime]:
        if not instance_ids:
            return []
        inst_uuids = [_uuid(i) for i in instance_ids]
        rows = self.session.scalars(
            select(OrmDecision.candle_timestamp).where(
                OrmDecision.strategy_instance_id.in_(inst_uuids)
            )
        ).all()
        return list(rows)

    def get_timeframe_execution_status(
        self,
        instrument_id: str,
        timeframe: str,
        instance_ids: list[str],
        now: datetime,
        *,
        candles: list[DomainCandle] | None = None,
        processed_timestamps: set[datetime] | None = None,
    ) -> dict[str, Any]:
        from quantara_engine.execution.catch_up import compute_backlog_status

        if candles is None:
            candles = self.list_recent_candles(instrument_id, timeframe, limit=500)
        if processed_timestamps is None:
            window_start = candles[0].timestamp if candles else None
            processed_timestamps = self.fully_processed_candle_timestamps(
                instance_ids,
                instrument_id,
                since=window_start,
            )
        last_processed = max(processed_timestamps) if processed_timestamps else None
        return compute_backlog_status(
            candles,
            timeframe,
            last_processed=last_processed,
            now=now,
        )

    def persist_exit_execution(
        self,
        *,
        order: Order,
        fill: FillResult,
        position: Position,
        trade: Trade,
        portfolio_state: PortfolioState,
        strategy_instance_id: str,
        filled_at: datetime,
        flush: bool = True,
    ) -> None:
        self.save_order(
            order,
            strategy_instance_id=strategy_instance_id,
            signal_id=None,
            flush=flush,
        )
        self.save_fill(
            fill_id=new_id(),
            order_id=order.id,
            fill=fill,
            side="exit",
            filled_at=filled_at,
            quantity=position.quantity,
            position_id=position.id,
        )
        if self.trade_exists_for_position(position.id):
            return
        self.update_position_closed(position.id, filled_at, fill.fill_price, flush=flush)
        self.save_trade(trade)
        self.sync_portfolios_financial_state_from_ledger([portfolio_state.portfolio], flush=flush)

    def persist_exit_executions_batch(
        self,
        bundles: list[dict[str, Any]],
    ) -> None:
        """Persist exit bundles in lock order: orders/fills → position closes → trades → portfolios."""
        if not bundles:
            return
        bundles = sorted(
            [b for b in bundles if not self.trade_exists_for_position(b["position"].id)],
            key=lambda b: b["position"].id,
        )
        if not bundles:
            return
        fill_rows: list[tuple[str, str, FillResult, datetime, Decimal, str]] = []
        closes: list[tuple[str, datetime, Decimal | None]] = []
        portfolios: dict[str, Portfolio] = {}
        snapshots: list[PortfolioSnapshot] = []

        for bundle in bundles:
            order = bundle["order"]
            fill = bundle["fill"]
            position = bundle["position"]
            trade = bundle["trade"]
            portfolio_state = bundle["portfolio_state"]
            strategy_instance_id = bundle["strategy_instance_id"]
            filled_at = bundle["filled_at"]

            self.save_order(
                order,
                strategy_instance_id=strategy_instance_id,
                signal_id=None,
                flush=False,
            )
            fill_id = new_id()
            fill_rows.append(
                (fill_id, order.id, fill, filled_at, position.quantity, position.id)
            )
            closes.append((position.id, filled_at, fill.fill_price))
            decision = bundle.get("decision")
            if decision is not None:
                self.save_decision(decision)
            snap = bundle.get("snapshot")
            if snap is not None:
                snapshots.append(snap)
            portfolios[portfolio_state.portfolio.id] = portfolio_state.portfolio

        # Orders must exist before fills (FK).
        self.session.flush()

        for fill_id, order_id, fill, filled_at, quantity, position_id in fill_rows:
            self.save_fill(
                fill_id=fill_id,
                order_id=order_id,
                fill=fill,
                side="exit",
                filled_at=filled_at,
                quantity=quantity,
                position_id=position_id,
            )

        self.update_positions_closed_batch(closes)

        for bundle in bundles:
            self.save_trade(bundle["trade"])

        self.session.flush()
        if snapshots:
            self.save_snapshots_batch(snapshots)
        # Caller syncs financial state after position marks when batched via PM.

    def cancel_pending_intent(self, intent_id: str, reason: str) -> None:
        self.update_order_intent_status(intent_id, IntentStatus.EXPIRED, reason)

    def cleanup_duplicate_pending_intents(
        self,
        experiment_id: str,
    ) -> dict[str, int]:
        """Cancel duplicate pending intents, keeping the oldest per instance+candle+direction."""
        from quantara_engine.models.trading import OrderIntent as OrmOrderIntentModel

        rows = self.session.scalars(
            select(OrmOrderIntentModel)
            .join(OrmStrategyInstance, OrmStrategyInstance.id == OrmOrderIntentModel.strategy_instance_id)
            .where(
                OrmStrategyInstance.experiment_id == _uuid(experiment_id),
                OrmOrderIntentModel.status == OrderIntentStatus.PENDING_EXECUTION,
                OrmOrderIntentModel.backtest_run_id.is_(None),
            )
            .order_by(OrmOrderIntentModel.created_at)
        ).all()

        seen: dict[tuple, str] = {}
        cancelled = 0
        kept = 0
        for row in rows:
            key = (
                str(row.strategy_instance_id),
                row.signal_candle_timestamp.isoformat(),
                row.direction.value,
            )
            if key in seen:
                row.status = OrderIntentStatus.REJECTED
                row.rejection_reason = "duplicate_intent"
                cancelled += 1
            else:
                seen[key] = str(row.id)
                kept += 1
        self.session.flush()
        return {"kept": kept, "cancelled": cancelled, "before": len(rows)}

    def cancel_stale_pending_intents(
        self,
        experiment_id: str,
        now: datetime,
    ) -> int:
        """Cancel pending intents whose execution candle is complete but never filled."""
        from quantara_engine.execution.timing import intent_past_execution_window
        from quantara_engine.models.trading import OrderIntent as OrmOrderIntentModel

        rows = self.session.scalars(
            select(OrmOrderIntentModel)
            .join(OrmStrategyInstance, OrmStrategyInstance.id == OrmOrderIntentModel.strategy_instance_id)
            .where(
                OrmStrategyInstance.experiment_id == _uuid(experiment_id),
                OrmOrderIntentModel.status == OrderIntentStatus.PENDING_EXECUTION,
                OrmOrderIntentModel.backtest_run_id.is_(None),
            )
        ).all()

        cancelled = 0
        for row in rows:
            instance = self.session.get(OrmStrategyInstance, row.strategy_instance_id)
            if not instance:
                continue
            tf = instance.timeframe.value if hasattr(instance.timeframe, "value") else str(instance.timeframe)
            if intent_past_execution_window(
                signal_candle_timestamp=row.signal_candle_timestamp,
                execution_candle_timestamp=row.execution_candle_timestamp,
                intent_created_at=row.created_at,
                timeframe=tf,
                now=now,
            ):
                row.status = OrderIntentStatus.EXPIRED
                row.rejection_reason = "execution_window_passed"
                cancelled += 1
        self.session.flush()
        return cancelled

    def delete_invalid_competition_snapshots(
        self,
        experiment_id: str,
        started_at: datetime,
    ) -> int:
        deleted = self.session.execute(
            text(
                """
                DELETE FROM portfolio_snapshots ps
                USING portfolios p, strategy_instances si
                WHERE ps.portfolio_id = p.id
                  AND si.portfolio_id = p.id
                  AND si.experiment_id = :exp
                  AND ps.timestamp >= :started_at
                  AND (
                    (ps.open_positions_count = 0 AND ps.equity <> p.initial_capital)
                    OR (ps.open_positions_count > 0 AND ps.unrealized_pnl <= -1000)
                  )
                """
            ),
            {"exp": experiment_id, "started_at": started_at},
        )
        self.session.flush()
        return deleted.rowcount or 0

    def list_competition_trades(
        self,
        experiment_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from quantara_engine.competition.constants import PORTFOLIO_DEF_BY_ID, RISK_SLUG_HE, TIMEFRAME_HE
        from quantara_engine.competition.multi_strategy_constants import (
            PORTFOLIO_DEF_BY_ID as MULTI_PORTFOLIO_DEF_BY_ID,
        )
        from quantara_engine.competition.orb_constants import ORB_PORTFOLIO_DEF_BY_ID

        rows = self.session.execute(
            select(OrmTrade, OrmStrategyInstance, OrmPortfolio, OrmRiskProfile, OrmInstrument)
            .join(OrmStrategyInstance, OrmStrategyInstance.id == OrmTrade.strategy_instance_id)
            .join(OrmPortfolio, OrmPortfolio.id == OrmTrade.portfolio_id)
            .join(OrmRiskProfile, OrmRiskProfile.id == OrmStrategyInstance.risk_profile_id)
            .join(OrmInstrument, OrmInstrument.id == OrmTrade.instrument_id)
            .where(
                OrmStrategyInstance.experiment_id == _uuid(experiment_id),
                OrmTrade.backtest_run_id.is_(None),
            )
            .order_by(OrmTrade.closed_at.desc())
            .limit(limit)
        ).all()

        results: list[dict[str, Any]] = []
        for trade, instance, portfolio, risk, instrument in rows:
            pid = str(portfolio.id)
            portfolio_def = PORTFOLIO_DEF_BY_ID.get(pid)
            orb_def = ORB_PORTFOLIO_DEF_BY_ID.get(pid)
            multi_def = MULTI_PORTFOLIO_DEF_BY_ID.get(pid)
            display_name = (
                portfolio_def.name_he
                if portfolio_def
                else orb_def.name_he
                if orb_def
                else multi_def.name_he
                if multi_def
                else portfolio.name
            )
            results.append(
                {
                    "trade_id": str(trade.id),
                    "portfolio_id": pid,
                    "portfolio_name": display_name,
                    "instrument": instrument.symbol,
                    "symbol": instrument.symbol,
                    "timeframe": instance.timeframe,
                    "timeframe_he": TIMEFRAME_HE.get(instance.timeframe, instance.timeframe),
                    "risk_slug": risk.slug,
                    "risk_name_he": RISK_SLUG_HE.get(risk.slug, portfolio.name),
                    "direction": trade.direction.value,
                    "entry_price": float(trade.entry_price),
                    "exit_price": float(trade.exit_price),
                    "quantity": float(trade.quantity),
                    "realized_pnl": float(trade.realized_pnl),
                    "exit_reason": trade.exit_reason.value,
                    "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                    "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
                }
            )
        return results

    def get_competition_today_stats(self) -> dict[str, int]:
        """Aggregate meaningful competition activity for Home (not raw HOLD spam)."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        instance_ids = self.list_competition_instance_ids()
        _, _, all_entries = self.list_all_competition_entries()
        portfolio_ids = [e["portfolio"].id for e in all_entries]
        if not instance_ids:
            return {
                "market_checks_today": 0,
                "entry_signals_today": 0,
                "strategy_signals_today": 0,
                "trades_opened_today": 0,
                "trades_closed_today": 0,
            }

        from quantara_engine.competition.paper_run import (
            decision_scope_clause,
            intent_scope_clause,
            position_scope_clause,
            trade_scope_clause,
        )

        inst_uuids = [_uuid(i) for i in instance_ids]
        port_uuids = [_uuid(i) for i in portfolio_ids]

        signal_types = [
            OrmDecisionType.BUY_SIGNAL,
            OrmDecisionType.SELL_SIGNAL,
            OrmDecisionType.NO_SETUP,
            OrmDecisionType.CLOSE_SIGNAL,
        ]
        market_checks = self.session.scalar(
            select(func.count())
            .select_from(OrmDecision)
            .where(
                OrmDecision.strategy_instance_id.in_(inst_uuids),
                OrmDecision.created_at >= today_start,
                OrmDecision.decision_type.in_(signal_types),
                decision_scope_clause(self),
            )
        ) or 0

        strategy_signals = self.session.scalar(
            select(func.count())
            .select_from(OrmDecision)
            .where(
                OrmDecision.strategy_instance_id.in_(inst_uuids),
                OrmDecision.created_at >= today_start,
                OrmDecision.decision_type.in_(
                    [OrmDecisionType.BUY_SIGNAL, OrmDecisionType.SELL_SIGNAL]
                ),
                decision_scope_clause(self),
            )
        ) or 0

        from quantara_engine.models.trading import OrderIntent as OrmOrderIntent

        # Actionable entries: risk-approved intents that reached the execution queue.
        entry_signals = self.session.scalar(
            select(func.count())
            .select_from(OrmOrderIntent)
            .where(
                OrmOrderIntent.portfolio_id.in_(port_uuids),
                OrmOrderIntent.created_at >= today_start,
                intent_scope_clause(self),
                OrmOrderIntent.target_risk_amount > 0,
                OrmOrderIntent.status.in_(
                    [
                        OrderIntentStatus.PENDING_EXECUTION,
                        OrderIntentStatus.EXECUTED,
                    ]
                ),
            )
        ) or 0

        from quantara_engine.models.trading import Position as OrmPosition
        from quantara_engine.models.enums import PositionStatus as OrmPositionStatus

        trades_opened = self.session.scalar(
            select(func.count())
            .select_from(OrmPosition)
            .where(
                OrmPosition.portfolio_id.in_(port_uuids),
                OrmPosition.opened_at >= today_start,
                OrmPosition.status == OrmPositionStatus.OPEN,
                position_scope_clause(self),
            )
        ) or 0

        trades_closed = self.session.scalar(
            select(func.count())
            .select_from(OrmTrade)
            .where(
                OrmTrade.portfolio_id.in_(port_uuids),
                OrmTrade.closed_at >= today_start,
                trade_scope_clause(self),
            )
        ) or 0

        return {
            "market_checks_today": int(market_checks),
            "entry_signals_today": int(entry_signals),
            "strategy_signals_today": int(strategy_signals),
            "sell_signals_today": int(
                self.session.scalar(
                    select(func.count())
                    .select_from(OrmDecision)
                    .where(
                        OrmDecision.strategy_instance_id.in_(inst_uuids),
                        OrmDecision.created_at >= today_start,
                        OrmDecision.decision_type == OrmDecisionType.SELL_SIGNAL,
                        decision_scope_clause(self),
                    )
                )
                or 0
            ),
            "trades_opened_today": int(trades_opened),
            "trades_closed_today": int(trades_closed),
            "realized_pnl_today": float(
                self.session.scalar(
                    select(func.coalesce(func.sum(OrmTrade.realized_pnl), 0)).where(
                        OrmTrade.portfolio_id.in_(port_uuids),
                        OrmTrade.closed_at >= today_start,
                        trade_scope_clause(self),
                    )
                )
                or 0
            ),
            "unrealized_pnl_total": float(
                self.session.scalar(
                    select(func.coalesce(func.sum(OrmPortfolio.unrealized_pnl), 0)).where(
                        OrmPortfolio.id.in_(port_uuids)
                    )
                )
                or 0
            ),
        }

    # ------------------------------------------------------------------ Worker

    def upsert_worker_job(
        self,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        scheduled_at: datetime,
    ) -> OrmWorkerJob | None:
        existing = self.session.scalar(
            select(OrmWorkerJob).where(OrmWorkerJob.idempotency_key == idempotency_key)
        )
        if existing:
            return existing

        row = OrmWorkerJob(
            id=uuid.uuid4(),
            job_type=JobType(job_type),
            idempotency_key=idempotency_key,
            payload=payload,
            status=WorkerJobStatus.PENDING,
            scheduled_at=scheduled_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_worker_run(
        self,
        run_id: str,
        worker_name: str,
        started_at: datetime,
        status: str = "success",
        jobs_processed: int = 0,
        errors: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        row = OrmWorkerRun(
            id=_uuid(run_id),
            worker_name=worker_name,
            started_at=started_at,
            completed_at=completed_at or datetime.now(timezone.utc),
            status=coerce_worker_run_status(status),
            jobs_processed=jobs_processed,
            errors=errors,
        )
        self.session.add(row)
        self.session.flush()

    def update_worker_status(self, worker_name: str, status: dict[str, Any]) -> None:
        key = f"worker_status:{worker_name}"
        self.update_settings(key, status)

        self.session.add(
            OrmEvent(
                id=uuid.uuid4(),
                event_type="worker_status",
                entity_type="worker",
                entity_id=uuid.uuid5(uuid.NAMESPACE_DNS, worker_name),
                payload={"worker_name": worker_name, **status},
            )
        )
        self.session.flush()

    # ------------------------------------------------------------------ Internal helpers

    def _ensure_exit_intent(
        self,
        order: Order,
        strategy_instance_id: str,
        signal_id: str | None,
    ) -> str:
        intent_id = str(uuid.uuid4())
        sig_id = signal_id or str(uuid.uuid4())
        idempotency_key = f"exit:{order.id}"

        instance = self.session.get(OrmStrategyInstance, _uuid(strategy_instance_id))
        if not instance:
            raise ValueError(f"Strategy instance not found: {strategy_instance_id}")

        if not self.session.get(OrmSignal, _uuid(sig_id)):
            self.session.merge(
                OrmSignal(
                    id=_uuid(sig_id),
                    strategy_instance_id=_uuid(strategy_instance_id),
                    strategy_version_id=instance.strategy_version_id,
                    instrument_id=_uuid(order.instrument_id),
                    candle_timestamp=order.submitted_at or datetime.now(timezone.utc),
                    action=OrmSignalAction.CLOSE,
                    reason="system_exit",
                    mode=_mode_to_orm(self.mode),
                    backtest_run_id=self._bt_uuid(),
                )
            )
            self.session.flush()

        row = OrmOrderIntent(
            id=_uuid(intent_id),
            signal_id=_uuid(sig_id),
            strategy_instance_id=_uuid(strategy_instance_id),
            portfolio_id=_uuid(order.portfolio_id),
            direction=OrmDirection(order.direction.value),
            quantity=order.quantity,
            entry_type=EntryType.MARKET,
            stop_loss=Decimal("0"),
            take_profit=None,
            target_risk_amount=Decimal("0"),
            actual_risk_amount=Decimal("0"),
            signal_candle_timestamp=order.submitted_at or datetime.now(timezone.utc),
            execution_candle_timestamp=order.submitted_at,
            risk_profile_id=instance.risk_profile_id,
            status=OrderIntentStatus.EXECUTED,
            idempotency_key=idempotency_key,
            mode=_mode_to_orm(self.mode),
            backtest_run_id=self._bt_uuid(),
        )
        self.session.merge(row)
        self.session.flush()
        return intent_id

    def _instrument_to_domain(self, row: OrmInstrument) -> Instrument:
        return Instrument(
            id=_str_id(row.id),
            symbol=row.symbol,
            name=row.name,
            asset_class=row.asset_class.value if hasattr(row.asset_class, "value") else str(row.asset_class),
            base_currency=row.base_currency,
            quote_currency=row.quote_currency,
            pip_size=row.pip_size,
            contract_size=row.contract_size,
            price_tick_size=row.price_tick_size,
            quantity_step=row.quantity_step,
            min_quantity=row.min_quantity,
            is_active=row.is_active,
        )

    def _risk_profile_to_domain(self, row: OrmRiskProfile) -> RiskProfile:
        slug = row.slug.value if hasattr(row.slug, "value") else str(row.slug)
        return RiskProfile(
            id=_str_id(row.id),
            slug=slug,
            name=row.name,
            risk_per_trade_pct=row.risk_per_trade_pct,
            max_open_positions=row.max_open_positions,
            max_total_exposure_pct=row.max_total_exposure_pct,
            daily_loss_limit_pct=row.daily_loss_limit_pct,
            max_drawdown_pct=row.max_drawdown_pct,
            parameters=row.parameters or {},
        )

    def _portfolio_to_domain(self, row: OrmPortfolio) -> Portfolio:
        return Portfolio(
            id=_str_id(row.id),
            name=row.name,
            mode=_mode_from_orm(row.mode),
            initial_capital=row.initial_capital,
            balance=row.balance,
            unrealized_pnl=row.unrealized_pnl,
            equity=row.equity,
            exposure_notional=row.exposure_notional,
            reserved_capital=row.reserved_capital,
            currency=row.currency,
            status=PortfolioStatus(row.status.value),
            halt_reason=row.halt_reason,
            peak_equity=row.peak_equity,
        )

    def _strategy_instance_to_domain(
        self,
        row: OrmStrategyInstance,
        strategy_slug: str,
        *,
        version_row: OrmStrategyVersion | None = None,
    ) -> StrategyInstance:
        if version_row is None:
            version_row = self.session.get(OrmStrategyVersion, row.strategy_version_id)
        version = version_row.version if version_row else "1.0.0"
        return StrategyInstance(
            id=_str_id(row.id),
            portfolio_id=_str_id(row.portfolio_id),
            strategy_version_id=_str_id(row.strategy_version_id),
            strategy_slug=strategy_slug,
            strategy_version=version,
            instrument_id=_str_id(row.instrument_id),
            timeframe=row.timeframe,
            risk_profile_id=_str_id(row.risk_profile_id),
            parameter_overrides=row.parameter_overrides or {},
            is_active=row.is_active,
        )

    def _candle_to_domain(self, row: OrmCandle) -> DomainCandle:
        return DomainCandle(
            instrument_id=_str_id(row.instrument_id),
            timeframe=row.timeframe,
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            source=row.source,
            is_complete=row.is_complete,
        )

    def _order_intent_to_domain(self, row: OrmOrderIntent) -> OrderIntent:
        return OrderIntent(
            id=_str_id(row.id),
            signal_id=_str_id(row.signal_id),
            strategy_instance_id=_str_id(row.strategy_instance_id),
            portfolio_id=_str_id(row.portfolio_id),
            direction=Direction(row.direction.value),
            quantity=row.quantity,
            stop_loss=row.stop_loss,
            take_profit=row.take_profit,
            target_risk_amount=row.target_risk_amount,
            actual_risk_amount=row.actual_risk_amount,
            signal_candle_timestamp=row.signal_candle_timestamp,
            execution_candle_timestamp=row.execution_candle_timestamp,
            risk_profile_id=_str_id(row.risk_profile_id),
            status=IntentStatus(row.status.value),
            entry_type=row.entry_type.value,
            limit_price=row.limit_price,
        )

    def _position_to_domain(self, row: OrmPosition) -> Position:
        return Position(
            id=_str_id(row.id),
            portfolio_id=_str_id(row.portfolio_id),
            strategy_instance_id=_str_id(row.strategy_instance_id),
            instrument_id=_str_id(row.instrument_id),
            direction=Direction(row.direction.value),
            quantity=row.quantity,
            entry_price=row.entry_price,
            stop_loss=row.stop_loss,
            take_profit=row.take_profit,
            current_price=row.current_price,
            unrealized_pnl=row.unrealized_pnl,
            status=PositionStatus(row.status.value),
            opened_at=row.opened_at,
            closed_at=row.closed_at,
        )

    def _trade_to_domain(self, row: OrmTrade) -> Trade:
        return Trade(
            id=_str_id(row.id),
            position_id=_str_id(row.position_id),
            portfolio_id=_str_id(row.portfolio_id),
            strategy_instance_id=_str_id(row.strategy_instance_id),
            strategy_version_id=_str_id(row.strategy_version_id),
            instrument_id=_str_id(row.instrument_id),
            direction=Direction(row.direction.value),
            quantity=row.quantity,
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            gross_pnl=row.gross_pnl,
            realized_pnl=row.realized_pnl,
            fees_total=row.fees_total,
            slippage_total=row.slippage_total,
            spread_total=row.spread_total,
            target_risk_amount=row.target_risk_amount,
            actual_risk_amount=row.actual_risk_amount,
            exit_reason=ExitReason(row.exit_reason.value),
            duration_seconds=row.duration_seconds,
            opened_at=row.opened_at,
            closed_at=row.closed_at,
        )

    def _snapshot_to_domain(self, row: OrmPortfolioSnapshot) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            portfolio_id=_str_id(row.portfolio_id),
            timestamp=row.timestamp,
            balance=row.balance,
            equity=row.equity,
            exposure_notional=row.exposure_notional,
            reserved_capital=row.reserved_capital,
            unrealized_pnl=row.unrealized_pnl,
            drawdown_pct=row.drawdown_pct,
            open_positions_count=row.open_positions_count,
        )

    def _decision_to_domain(self, row: OrmDecision) -> DecisionLogEntry:
        from quantara_engine.domain.types import DecisionType

        return DecisionLogEntry(
            id=_str_id(row.id),
            strategy_instance_id=_str_id(row.strategy_instance_id),
            instrument_id=_str_id(row.instrument_id),
            candle_timestamp=row.candle_timestamp,
            decision_type=DecisionType(row.decision_type.value),
            message=row.message,
            signal_id=_str_id(row.signal_id) if row.signal_id else None,
            metadata=row.metadata_ or {},
        )

    def _backtest_to_dict(self, row: OrmBacktestRun) -> dict[str, Any]:
        return {
            "id": _str_id(row.id),
            "portfolio_id": _str_id(row.portfolio_id),
            "strategy_instance_id": _str_id(row.strategy_instance_id),
            "strategy_version_id": _str_id(row.strategy_version_id),
            "instrument_id": _str_id(row.instrument_id),
            "timeframe": row.timeframe,
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "initial_capital": float(row.initial_capital),
            "final_capital": float(row.final_capital) if row.final_capital else None,
            "status": row.status.value,
            "parameters": row.parameters,
            "execution_assumptions": row.execution_assumptions,
            "dataset_fingerprint": row.dataset_fingerprint,
            "metrics": row.metrics,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "error_message": row.error_message,
        }
