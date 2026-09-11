"""Canonical broker execution — orders, fills, positions, account are DB truth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.attribution import allocate_fill_to_strategy_legs
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.margin import (
    initial_margin_for_notional,
    maintenance_margin_for_notional,
    quote_notional_usd,
)
from quantara_engine.broker.market_gate import data_fresh_for_instrument, market_open_for_instrument
from quantara_engine.broker.netting import apply_fill_with_realized_pnl
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import (
    BrokerOrderDecision,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerRejectionReason,
    PositionMode,
)
from quantara_engine.domain.types import Direction, Instrument, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates

PAPER_ACCOUNT_SLUG = "quantara_paper_competition"


@dataclass
class BrokerExecutionResult:
    accepted: bool
    broker_order_id: str | None = None
    broker_fill_id: str | None = None
    decision: BrokerOrderDecision | None = None
    realized_pnl: Decimal = Decimal("0")


class BrokerExecutionService:
    """Persisted paper broker — NETTING mode only."""

    def __init__(self, store: TradingStore) -> None:
        self.store = store
        self.profile = QUANTARA_STANDARD_PAPER

    def _tables_ready(self) -> bool:
        try:
            self.store.session.execute(text("SELECT 1 FROM broker_accounts LIMIT 1"))
            return True
        except Exception:
            return False

    def get_account_id(self) -> str | None:
        if not self._tables_ready():
            return None
        row = self.store.session.execute(
            text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
            {"slug": PAPER_ACCOUNT_SLUG},
        ).first()
        return row[0] if row else None

    def load_account_snapshot(self, account_id: str | None = None):
        account_id = account_id or self.get_account_id()
        if not account_id:
            return build_account_snapshot(
                cash=self.profile.starting_cash,
                balance=self.profile.starting_cash,
                realized_pnl=Decimal("0"),
                positions={},
                fx_rates={"USD": Decimal("1"), "JPY": Decimal("150")},
                profile=self.profile,
                spot_crypto_cash=self.profile.starting_cash,
            )

        acct = self.store.session.execute(
            text(
                """
                SELECT cash, balance, realized_pnl, spot_crypto_cash
                FROM broker_accounts WHERE id = :id
                """
            ),
            {"id": account_id},
        ).mappings().first()

        rows = self.store.session.execute(
            text(
                """
                SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = :aid
                """
            ),
            {"aid": account_id},
        ).mappings().all()

        positions = {
            str(r["symbol"]).upper(): (
                Decimal(str(r["net_quantity"])),
                Decimal(str(r["average_price"])),
                Decimal(str(r["mark_price"])),
            )
            for r in rows
        }
        symbols = list(positions.keys())
        instruments = [self.store.get_instrument_by_symbol(s) for s in symbols]
        instruments = [i for i in instruments if i]
        fx = resolve_dashboard_fx_rates(self.store, quote_currencies_for_instruments(instruments))
        fx_map = {k: v for k, v in fx.quote_per_usd.items()}

        return build_account_snapshot(
            cash=Decimal(str(acct["cash"])),
            balance=Decimal(str(acct["balance"])),
            realized_pnl=Decimal(str(acct["realized_pnl"])),
            positions=positions,
            fx_rates=fx_map,
            profile=self.profile,
            spot_crypto_cash=Decimal(str(acct["spot_crypto_cash"])),
        )

    def _persist_account_metrics(self, account_id: str, snapshot) -> None:
        self.store.session.execute(
            text(
                """
                UPDATE broker_accounts SET
                  cash = :cash,
                  balance = :balance,
                  equity = :equity,
                  realized_pnl = :realized,
                  unrealized_pnl = :unrealized,
                  gross_exposure = :gross,
                  net_exposure = :net,
                  initial_margin_used = :im,
                  maintenance_margin_required = :mm,
                  free_margin = :fm,
                  available_margin = :am,
                  spot_crypto_cash = :sc,
                  account_state = :state,
                  updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": account_id,
                "cash": snapshot.cash,
                "balance": snapshot.balance,
                "equity": snapshot.equity,
                "realized": snapshot.realized_pnl,
                "unrealized": snapshot.unrealized_pnl,
                "gross": snapshot.gross_exposure,
                "net": snapshot.net_exposure,
                "im": snapshot.initial_margin_used,
                "mm": snapshot.maintenance_margin_required,
                "fm": snapshot.free_margin,
                "am": snapshot.available_margin,
                "sc": snapshot.spot_crypto_cash,
                "state": snapshot.account_state.value,
            },
        )

    def execute_intent(
        self,
        intent: OrderIntent,
        instrument: Instrument,
        fill: FillResult,
        *,
        execution_at: datetime,
        timeframe: str,
        idempotency_key: str,
        opportunity_key: str | None = None,
        strategy_position_id: str | None = None,
    ) -> BrokerExecutionResult:
        """
        Final authoritative broker validation + fill persistence.

        Called at execution time only.
        """
        if not self._tables_ready():
            # Migration not applied — reject safely
            decision = BrokerOrderDecision(
                accepted=False,
                accepted_quantity=Decimal("0"),
                rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                rejection_detail="broker tables not available — apply migration 0006",
            )
            return BrokerExecutionResult(accepted=False, decision=decision)

        account_id = self.get_account_id()
        if not account_id:
            decision = BrokerOrderDecision(
                accepted=False,
                accepted_quantity=Decimal("0"),
                rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                rejection_detail="paper broker account missing",
            )
            return BrokerExecutionResult(accepted=False, decision=decision)

        # Idempotency — return existing fill if retry
        existing = self.store.session.execute(
            text(
                """
                SELECT o.id::text, o.status::text, f.id::text
                FROM broker_orders o
                LEFT JOIN broker_fills f ON f.broker_order_id = o.id
                WHERE o.broker_account_id = :aid AND o.idempotency_key = :key
                """
            ),
            {"aid": account_id, "key": idempotency_key},
        ).mappings().first()
        if existing:
            if existing["status"] == "filled" and existing["id"]:
                return BrokerExecutionResult(
                    accepted=True,
                    broker_order_id=existing["id"],
                    broker_fill_id=existing.get("id") if False else None,
                )

        market_open = market_open_for_instrument(instrument, execution_at)
        data_fresh, _ = data_fresh_for_instrument(self.store, instrument, timeframe, execution_at)

        snapshot = self.load_account_snapshot(account_id)
        fx = resolve_dashboard_fx_rates(self.store, quote_currencies_for_instruments([instrument]))
        fx_map = {k: v for k, v in fx.quote_per_usd.items()}

        direction = "long" if intent.direction == Direction.LONG else "short"
        request = BrokerOrderRequest(
            symbol=instrument.symbol,
            asset_class=str(instrument.asset_class),
            direction=direction,
            quantity=intent.quantity,
            mark_price=fill.fill_price,
            is_close=intent.is_close,
            strategy_portfolio_id=intent.portfolio_id,
            opportunity_key=opportunity_key,
            market_open=market_open,
            data_fresh=data_fresh,
        )
        decision = evaluate_broker_order(snapshot, self.profile, request, fx_map)

        order_id = str(uuid.uuid4())
        try:
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_orders (
                      id, broker_account_id, strategy_intent_id, strategy_portfolio_id,
                      instrument_id, direction, requested_quantity, accepted_quantity,
                      status, idempotency_key, submitted_at
                    ) VALUES (
                      :id, :aid, :iid, :pid, :inst, :dir, :qty, 0,
                      'validating', :key, :sub
                    )
                    """
                ),
                {
                    "id": order_id,
                    "aid": account_id,
                    "iid": intent.id,
                    "pid": intent.portfolio_id,
                    "inst": instrument.id,
                    "dir": direction,
                    "qty": intent.quantity,
                    "key": idempotency_key,
                    "sub": execution_at,
                },
            )
        except IntegrityError:
            self.store.session.rollback()
            dup = self.store.session.execute(
                text(
                    """
                    SELECT id::text, status::text FROM broker_orders
                    WHERE broker_account_id = :aid AND idempotency_key = :key
                    """
                ),
                {"aid": account_id, "key": idempotency_key},
            ).mappings().first()
            if dup and dup["status"] == "filled":
                return BrokerExecutionResult(accepted=True, broker_order_id=dup["id"])
            return BrokerExecutionResult(accepted=False, decision=decision)

        if not decision.accepted:
            reason = decision.rejection_reason.value if decision.rejection_reason else "unknown"
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_orders SET status = 'rejected',
                      rejection_reason = :reason, rejection_detail = :detail,
                      updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": order_id, "reason": reason, "detail": decision.rejection_detail[:2000]},
            )
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_order_rejections (
                      broker_account_id, broker_order_id, strategy_portfolio_id,
                      symbol, quantity, reason, detail, opportunity_key
                    ) VALUES (:aid, :oid, :pid, :sym, :qty, :reason, :detail, :opp)
                    """
                ),
                {
                    "aid": account_id,
                    "oid": order_id,
                    "pid": intent.portfolio_id,
                    "sym": instrument.symbol,
                    "qty": intent.quantity,
                    "reason": reason,
                    "detail": decision.rejection_detail,
                    "opp": opportunity_key,
                },
            )
            self.store.session.flush()
            return BrokerExecutionResult(accepted=False, broker_order_id=order_id, decision=decision)

        # Accept + fill
        pos_row = self.store.session.execute(
            text(
                """
                SELECT id::text, net_quantity, average_price
                FROM broker_positions
                WHERE broker_account_id = :aid AND instrument_id = :iid
                """
            ),
            {"aid": account_id, "iid": instrument.id},
        ).mappings().first()

        cur_qty = Decimal(str(pos_row["net_quantity"])) if pos_row else Decimal("0")
        cur_avg = Decimal(str(pos_row["average_price"])) if pos_row else Decimal("0")

        netting = apply_fill_with_realized_pnl(
            cur_qty,
            cur_avg,
            decision.accepted_quantity,
            fill.fill_price,
            direction,
            mode=PositionMode.NETTING,
        )

        spec = get_instrument_spec(instrument.symbol)
        rules = self.profile.rules_for(spec.asset_class)
        new_balance = snapshot.balance + netting.realized_pnl
        new_cash = snapshot.cash
        new_spot = snapshot.spot_crypto_cash

        if spec.asset_class == "crypto" and rules.initial_margin_pct >= Decimal("100"):
            notional = quote_notional_usd(decision.accepted_quantity, fill.fill_price, spec, fx_map)
            if direction == "long" and not intent.is_close:
                new_spot -= notional
                new_cash -= notional
            elif direction == "short" or intent.is_close:
                new_spot += notional
                new_cash += notional

        fill_id = str(uuid.uuid4())
        position_id = pos_row["id"] if pos_row else str(uuid.uuid4())

        if netting.new_net_qty == 0:
            if pos_row:
                self.store.session.execute(
                    text("DELETE FROM broker_positions WHERE id = :id"),
                    {"id": position_id},
                )
            position_id = None
        elif pos_row:
            notional = quote_notional_usd(netting.new_net_qty, fill.fill_price, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_positions SET
                      net_quantity = :qty, average_price = :avg, mark_price = :mark,
                      initial_margin = :im, maintenance_margin = :mm, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": position_id,
                    "qty": netting.new_net_qty,
                    "avg": netting.new_avg_price,
                    "mark": fill.fill_price,
                    "im": initial_margin_for_notional(notional, rules),
                    "mm": maintenance_margin_for_notional(notional, rules),
                },
            )
        else:
            notional = quote_notional_usd(netting.new_net_qty, fill.fill_price, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_positions (
                      id, broker_account_id, instrument_id, net_quantity,
                      average_price, mark_price, initial_margin, maintenance_margin
                    ) VALUES (:id, :aid, :iid, :qty, :avg, :mark, :im, :mm)
                    """
                ),
                {
                    "id": position_id,
                    "aid": account_id,
                    "iid": instrument.id,
                    "qty": netting.new_net_qty,
                    "avg": netting.new_avg_price,
                    "mark": fill.fill_price,
                    "im": initial_margin_for_notional(notional, rules),
                    "mm": maintenance_margin_for_notional(notional, rules),
                },
            )

        self.store.session.execute(
            text(
                """
                UPDATE broker_orders SET status = 'filled', accepted_quantity = :qty,
                  updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": order_id, "qty": decision.accepted_quantity},
        )

        self.store.session.execute(
            text(
                """
                INSERT INTO broker_fills (
                  id, broker_order_id, broker_position_id, fill_sequence,
                  fill_price, fill_quantity, realized_pnl, fees, slippage, spread_cost, filled_at
                ) VALUES (:id, :oid, :pid, 1, :price, :qty, :pnl, :fees, :slip, :spread, :at)
                """
            ),
            {
                "id": fill_id,
                "oid": order_id,
                "pid": position_id,
                "price": fill.fill_price,
                "qty": decision.accepted_quantity,
                "pnl": netting.realized_pnl,
                "fees": fill.fees,
                "slip": fill.slippage,
                "spread": fill.spread_cost,
                "at": execution_at,
            },
        )

        allocate_fill_to_strategy_legs(
            self.store,
            broker_fill_id=fill_id,
            strategy_position_id=strategy_position_id,
            portfolio_id=intent.portfolio_id,
            direction=direction,
            quantity=decision.accepted_quantity,
            fill_price=fill.fill_price,
            realized_pnl=netting.realized_pnl,
            opportunity_key=opportunity_key,
        )

        updated_positions = {}
        for r in self.store.session.execute(
            text(
                """
                SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price
                FROM broker_positions bp JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = :aid
                """
            ),
            {"aid": account_id},
        ).mappings():
            updated_positions[str(r["symbol"]).upper()] = (
                Decimal(str(r["net_quantity"])),
                Decimal(str(r["average_price"])),
                Decimal(str(r["mark_price"])),
            )

        new_snapshot = build_account_snapshot(
            cash=new_cash,
            balance=new_balance,
            realized_pnl=snapshot.realized_pnl + netting.realized_pnl,
            positions=updated_positions,
            fx_rates=fx_map,
            profile=self.profile,
            spot_crypto_cash=new_spot,
        )
        self._persist_account_metrics(account_id, new_snapshot)
        self.store.session.flush()

        return BrokerExecutionResult(
            accepted=True,
            broker_order_id=order_id,
            broker_fill_id=fill_id,
            decision=decision,
            realized_pnl=netting.realized_pnl,
        )
