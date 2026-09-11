"""Canonical broker execution — orders, fills, positions, account are DB truth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
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
from quantara_engine.broker.pnl import unrealized_pnl_usd
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import (
    AccountState,
    BrokerOrderDecision,
    BrokerOrderRequest,
    BrokerRejectionReason,
    PositionMode,
)
from quantara_engine.domain.types import Direction, Instrument, IntentStatus, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates

PAPER_ACCOUNT_SLUG = "quantara_paper_competition"


@dataclass
class BrokerExecutionResult:
    accepted: bool
    broker_order_id: str | None = None
    broker_fill_id: str | None = None
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    decision: BrokerOrderDecision | None = None
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    from_existing_fill: bool = False


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

    def get_account_row(self) -> dict | None:
        if not self._tables_ready():
            return None
        return self.store.session.execute(
            text(
                """
                SELECT id::text, is_active, pending_owner_reset, account_state::text AS account_state,
                       cash, balance, realized_pnl, spot_crypto_cash
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": PAPER_ACCOUNT_SLUG},
        ).mappings().first()

    def get_account_id(self) -> str | None:
        row = self.get_account_row()
        return row["id"] if row else None

    def account_is_active(self) -> bool:
        row = self.get_account_row()
        return bool(row and row.get("is_active"))

    def _account_may_trade(self, row: dict) -> tuple[bool, str]:
        if not row.get("is_active"):
            return False, "broker account inactive"
        if row.get("pending_owner_reset"):
            return False, "pending owner reset"
        stored = str(row.get("account_state") or "paused")
        if stored == AccountState.PAUSED.value:
            return False, "account paused"
        return True, ""

    def load_account_snapshot(self, account_id: str | None = None):
        row = self.get_account_row()
        if not row:
            return build_account_snapshot(
                cash=Decimal("0"),
                balance=Decimal("0"),
                realized_pnl=Decimal("0"),
                positions={},
                fx_rates={"USD": Decimal("1"), "JPY": Decimal("150")},
                profile=self.profile,
                spot_crypto_cash=Decimal("0"),
            )
        account_id = account_id or row["id"]

        pos_rows = self.store.session.execute(
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
            for r in pos_rows
        }
        symbols = list(positions.keys())
        instruments = [self.store.get_instrument_by_symbol(s) for s in symbols]
        instruments = [i for i in instruments if i]
        fx = resolve_dashboard_fx_rates(self.store, quote_currencies_for_instruments(instruments))
        fx_map = {k: v for k, v in fx.quote_per_usd.items()}

        snap = build_account_snapshot(
            cash=Decimal(str(row["cash"])),
            balance=Decimal(str(row["balance"])),
            realized_pnl=Decimal(str(row["realized_pnl"])),
            positions=positions,
            fx_rates=fx_map,
            profile=self.profile,
            spot_crypto_cash=Decimal(str(row["spot_crypto_cash"])),
        )
        stored = str(row.get("account_state") or AccountState.PAUSED.value)
        if not row.get("is_active") or row.get("pending_owner_reset"):
            snap.account_state = AccountState.PAUSED
        elif stored == AccountState.PAUSED.value:
            snap.account_state = AccountState.PAUSED
        elif stored == AccountState.LIQUIDATION_PENDING.value:
            snap.account_state = AccountState.LIQUIDATION_PENDING
        elif stored == AccountState.LIQUIDATION.value:
            snap.account_state = AccountState.LIQUIDATION
        return snap

    def _persist_ledger_balances(
        self,
        account_id: str,
        *,
        cash: Decimal,
        balance: Decimal,
        realized_pnl: Decimal,
        spot_crypto_cash: Decimal,
    ) -> None:
        self.store.session.execute(
            text(
                """
                UPDATE broker_accounts SET
                  cash = :cash, balance = :balance, realized_pnl = :realized,
                  spot_crypto_cash = :spot, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": account_id,
                "cash": cash,
                "balance": balance,
                "realized": realized_pnl,
                "spot": spot_crypto_cash,
            },
        )

    def _resolve_persisted_account_state(self, row: dict, calculated) -> str:
        if not row.get("is_active") or row.get("pending_owner_reset"):
            return AccountState.PAUSED.value
        stored = str(row.get("account_state") or AccountState.PAUSED.value)
        if stored == AccountState.PAUSED.value:
            return AccountState.PAUSED.value
        if stored == AccountState.LIQUIDATION_PENDING.value:
            if calculated.account_state == AccountState.LIQUIDATION:
                return AccountState.LIQUIDATION_PENDING.value
            return calculated.account_state.value
        return calculated.account_state.value

    def _persist_account_metrics(self, account_id: str, snapshot, *, row: dict | None = None) -> None:
        row = row or self.get_account_row()
        state = self._resolve_persisted_account_state(row, snapshot) if row else snapshot.account_state.value
        self.store.session.execute(
            text(
                """
                UPDATE broker_accounts SET
                  cash = :cash, balance = :balance, equity = :equity,
                  realized_pnl = :realized, unrealized_pnl = :unrealized,
                  gross_exposure = :gross, net_exposure = :net,
                  initial_margin_used = :im, maintenance_margin_required = :mm,
                  free_margin = :fm, available_margin = :am, spot_crypto_cash = :sc,
                  account_state = :state, updated_at = NOW()
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
                "state": state,
            },
        )

    def _load_existing_fill(self, account_id: str, idempotency_key: str) -> BrokerExecutionResult | None:
        row = self.store.session.execute(
            text(
                """
                SELECT o.id::text AS order_id, o.status::text AS status,
                       f.id::text AS fill_id, f.fill_price, f.fill_quantity,
                       f.realized_pnl, f.fees, f.spread_cost, f.slippage
                FROM broker_orders o
                LEFT JOIN broker_fills f ON f.broker_order_id = o.id
                WHERE o.broker_account_id = :aid AND o.idempotency_key = :key
                """
            ),
            {"aid": account_id, "key": idempotency_key},
        ).mappings().first()
        if not row:
            return None
        if row["status"] == "filled" and row["fill_id"]:
            return BrokerExecutionResult(
                accepted=True,
                broker_order_id=row["order_id"],
                broker_fill_id=row["fill_id"],
                fill_price=Decimal(str(row["fill_price"])),
                fill_quantity=Decimal(str(row["fill_quantity"])),
                realized_pnl=Decimal(str(row["realized_pnl"])),
                fees=Decimal(str(row["fees"])),
                spread_cost=Decimal(str(row["spread_cost"] or 0)),
                slippage=Decimal(str(row["slippage"] or 0)),
                from_existing_fill=True,
            )
        if row["status"] == "rejected":
            return BrokerExecutionResult(accepted=False, broker_order_id=row["order_id"])
        return None

    def _execution_fees(self, fill: FillResult) -> Decimal:
        """Explicit commission only — spread/slippage are embedded in fill price."""
        return fill.fees.quantize(Decimal("0.0001"))

    def execute_order(
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
        order_purpose: str = "entry",
        is_liquidation: bool = False,
    ) -> BrokerExecutionResult:
        if not self._tables_ready():
            return BrokerExecutionResult(
                accepted=False,
                decision=BrokerOrderDecision(
                    accepted=False,
                    accepted_quantity=Decimal("0"),
                    rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                    rejection_detail="broker tables not available",
                ),
            )

        account_row = self.get_account_row()
        if not account_row:
            return BrokerExecutionResult(
                accepted=False,
                decision=BrokerOrderDecision(
                    accepted=False,
                    accepted_quantity=Decimal("0"),
                    rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                    rejection_detail="paper broker account missing",
                ),
            )

        account_id = account_row["id"]
        may_trade, block_reason = self._account_may_trade(account_row)
        if not may_trade:
            return BrokerExecutionResult(
                accepted=False,
                decision=BrokerOrderDecision(
                    accepted=False,
                    accepted_quantity=Decimal("0"),
                    rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                    rejection_detail=block_reason,
                ),
            )

        existing = self._load_existing_fill(account_id, idempotency_key)
        if existing:
            return existing

        market_open = market_open_for_instrument(instrument, execution_at)
        data_fresh, _ = data_fresh_for_instrument(self.store, instrument, timeframe, execution_at)
        snapshot = self.load_account_snapshot(account_id)
        fx = resolve_dashboard_fx_rates(self.store, quote_currencies_for_instruments([instrument]))
        fx_map = {k: v for k, v in fx.quote_per_usd.items()}

        direction = "long" if intent.direction == Direction.LONG else "short"
        is_close = intent.is_close or order_purpose in ("sl", "tp", "close", "flatten", "liquidation")
        request = BrokerOrderRequest(
            symbol=instrument.symbol,
            asset_class=str(instrument.asset_class),
            direction=direction,
            quantity=intent.quantity,
            mark_price=fill.fill_price,
            is_close=is_close,
            strategy_portfolio_id=intent.portfolio_id,
            opportunity_key=opportunity_key,
            market_open=market_open,
            data_fresh=data_fresh,
            is_liquidation=is_liquidation,
        )
        decision = evaluate_broker_order(snapshot, self.profile, request, fx_map)

        order_id = str(uuid.uuid4())
        try:
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_orders (
                      id, broker_account_id, strategy_intent_id, strategy_portfolio_id,
                      instrument_id, direction, requested_quantity, status,
                      idempotency_key, order_purpose, submitted_at
                    ) VALUES (
                      :id, :aid, :iid, :pid, :inst, :dir, :qty, 'validating',
                      :key, :purpose, :sub
                    )
                    """
                ),
                {
                    "id": order_id,
                    "aid": account_id,
                    "iid": getattr(intent, "id", None),
                    "pid": intent.portfolio_id,
                    "inst": instrument.id,
                    "dir": direction,
                    "qty": intent.quantity,
                    "key": idempotency_key,
                    "purpose": order_purpose,
                    "sub": execution_at,
                },
            )
        except IntegrityError:
            self.store.session.rollback()
            dup = self._load_existing_fill(account_id, idempotency_key)
            if dup:
                return dup
            return BrokerExecutionResult(accepted=False, decision=decision)

        if not decision.accepted:
            reason = decision.rejection_reason.value if decision.rejection_reason else "unknown"
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_orders SET status = 'rejected',
                      rejection_reason = :reason, rejection_detail = :detail, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": order_id, "reason": reason, "detail": (decision.rejection_detail or "")[:2000]},
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

        return self._apply_fill(
            account_id=account_id,
            order_id=order_id,
            instrument=instrument,
            direction=direction,
            quantity=decision.accepted_quantity,
            fill=fill,
            execution_at=execution_at,
            fx_map=fx_map,
            snapshot=snapshot,
            portfolio_id=intent.portfolio_id,
            strategy_position_id=strategy_position_id,
            opportunity_key=opportunity_key,
            is_close=is_close,
            order_purpose=order_purpose,
        )

    def _apply_fill(
        self,
        *,
        account_id: str,
        order_id: str,
        instrument: Instrument,
        direction: str,
        quantity: Decimal,
        fill: FillResult,
        execution_at: datetime,
        fx_map: dict[str, Decimal],
        snapshot,
        portfolio_id: str,
        strategy_position_id: str | None,
        opportunity_key: str | None,
        is_close: bool,
        order_purpose: str,
    ) -> BrokerExecutionResult:
        spec = get_instrument_spec(instrument.symbol)
        rules = self.profile.rules_for(spec.asset_class)
        fees = self._execution_fees(fill)

        pos_row = self.store.session.execute(
            text(
                """
                SELECT id::text, net_quantity, average_price
                FROM broker_positions WHERE broker_account_id = :aid AND instrument_id = :iid
                """
            ),
            {"aid": account_id, "iid": instrument.id},
        ).mappings().first()

        cur_qty = Decimal(str(pos_row["net_quantity"])) if pos_row else Decimal("0")
        cur_avg = Decimal(str(pos_row["average_price"])) if pos_row else Decimal("0")

        netting = apply_fill_with_realized_pnl(
            cur_qty, cur_avg, quantity, fill.fill_price, direction,
            mode=PositionMode.NETTING, spec=spec, fx_rates=fx_map,
        )

        new_balance = snapshot.balance + netting.realized_pnl - fees
        new_realized = snapshot.realized_pnl + netting.realized_pnl
        new_cash = snapshot.cash
        new_spot = snapshot.spot_crypto_cash

        if spec.asset_class == "crypto" and rules.initial_margin_pct >= Decimal("100"):
            notional = quote_notional_usd(quantity, fill.fill_price, spec, fx_map)
            if direction == "long" and not is_close:
                new_cash -= notional + fees
                new_spot -= notional + fees
            else:
                new_cash += notional - fees
                new_spot += notional - fees
        else:
            new_cash -= fees

        fill_id = str(uuid.uuid4())
        position_id = pos_row["id"] if pos_row else str(uuid.uuid4())

        if netting.new_net_qty == 0:
            if pos_row:
                self.store.session.execute(
                    text("DELETE FROM broker_positions WHERE id = :id"), {"id": position_id}
                )
            position_id = None
        elif pos_row:
            notional = quote_notional_usd(netting.new_net_qty, fill.fill_price, spec, fx_map)
            unreal = unrealized_pnl_usd(netting.new_net_qty, netting.new_avg_price, fill.fill_price, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_positions SET net_quantity = :qty, average_price = :avg,
                      mark_price = :mark, unrealized_pnl = :upnl,
                      initial_margin = :im, maintenance_margin = :mm, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": position_id,
                    "qty": netting.new_net_qty,
                    "avg": netting.new_avg_price,
                    "mark": fill.fill_price,
                    "upnl": unreal,
                    "im": initial_margin_for_notional(notional, rules),
                    "mm": maintenance_margin_for_notional(notional, rules),
                },
            )
        else:
            notional = quote_notional_usd(netting.new_net_qty, fill.fill_price, spec, fx_map)
            unreal = unrealized_pnl_usd(netting.new_net_qty, netting.new_avg_price, fill.fill_price, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_positions (
                      id, broker_account_id, instrument_id, net_quantity, average_price,
                      mark_price, unrealized_pnl, initial_margin, maintenance_margin
                    ) VALUES (:id, :aid, :iid, :qty, :avg, :mark, :upnl, :im, :mm)
                    """
                ),
                {
                    "id": position_id,
                    "aid": account_id,
                    "iid": instrument.id,
                    "qty": netting.new_net_qty,
                    "avg": netting.new_avg_price,
                    "mark": fill.fill_price,
                    "upnl": unreal,
                    "im": initial_margin_for_notional(notional, rules),
                    "mm": maintenance_margin_for_notional(notional, rules),
                },
            )

        self.store.session.execute(
            text(
                """
                UPDATE broker_orders SET status = 'filled', accepted_quantity = :qty, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": order_id, "qty": quantity},
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
                "qty": quantity,
                "pnl": netting.realized_pnl,
                "fees": fees,
                "slip": fill.slippage,
                "spread": fill.spread_cost,
                "at": execution_at,
            },
        )

        attributed_realized = allocate_fill_to_strategy_legs(
            self.store,
            broker_account_id=account_id,
            broker_fill_id=fill_id,
            symbol=instrument.symbol,
            strategy_position_id=strategy_position_id,
            portfolio_id=portfolio_id,
            direction=direction,
            quantity=quantity,
            fill_price=fill.fill_price,
            closed_quantity=netting.closed_quantity,
            fx_rates=fx_map,
            opportunity_key=opportunity_key,
        )
        self.store.session.execute(
            text("UPDATE broker_fills SET realized_pnl = :pnl WHERE id = :id"),
            {"id": fill_id, "pnl": attributed_realized},
        )

        self._persist_ledger_balances(
            account_id,
            cash=new_cash,
            balance=new_balance,
            realized_pnl=new_realized,
            spot_crypto_cash=new_spot,
        )
        self._refresh_account_from_db(account_id)
        self.store.session.flush()

        return BrokerExecutionResult(
            accepted=True,
            broker_order_id=order_id,
            broker_fill_id=fill_id,
            fill_price=fill.fill_price,
            fill_quantity=quantity,
            realized_pnl=netting.realized_pnl,
            fees=fees,
            spread_cost=fill.spread_cost,
            slippage=fill.slippage,
        )

    def _refresh_account_from_db(self, account_id: str) -> None:
        row = self.get_account_row()
        snapshot = self.load_account_snapshot(account_id)
        self._persist_account_metrics(account_id, snapshot, row=row)

    def mark_to_market(self, marks: dict[str, Decimal], *, at: datetime | None = None) -> None:
        """Update broker position marks and account metrics without a fill."""
        if not self._tables_ready():
            return
        account_id = self.get_account_id()
        if not account_id:
            return

        for symbol, mark in marks.items():
            instrument = self.store.get_instrument_by_symbol(symbol)
            if not instrument:
                continue
            spec = get_instrument_spec(symbol)
            fx = resolve_dashboard_fx_rates(self.store, quote_currencies_for_instruments([instrument]))
            fx_map = {k: v for k, v in fx.quote_per_usd.items()}
            row = self.store.session.execute(
                text(
                    """
                    SELECT id::text, net_quantity, average_price
                    FROM broker_positions bp
                    JOIN instruments i ON i.id = bp.instrument_id
                    WHERE bp.broker_account_id = :aid AND i.symbol = :sym
                    """
                ),
                {"aid": account_id, "sym": symbol.upper()},
            ).mappings().first()
            if not row:
                continue
            qty = Decimal(str(row["net_quantity"]))
            avg = Decimal(str(row["average_price"]))
            rules = self.profile.rules_for(spec.asset_class)
            notional = quote_notional_usd(qty, mark, spec, fx_map)
            unreal = unrealized_pnl_usd(qty, avg, mark, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_positions SET mark_price = :mark, unrealized_pnl = :upnl,
                      initial_margin = :im, maintenance_margin = :mm, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "mark": mark,
                    "upnl": unreal,
                    "im": initial_margin_for_notional(notional, rules),
                    "mm": maintenance_margin_for_notional(notional, rules),
                },
            )

        self._refresh_account_from_db(account_id)
        self.run_liquidation_if_required(at=at)

    def _set_account_state(self, account_id: str, state: AccountState) -> None:
        self.store.session.execute(
            text(
                """
                UPDATE broker_accounts SET account_state = :state, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": account_id, "state": state.value},
        )

    def run_liquidation_if_required(self, *, at: datetime | None = None) -> list[BrokerExecutionResult]:
        """Force-close positions when maintenance breached — largest maintenance first."""
        if not self._tables_ready() or not self.account_is_active():
            return []
        account_id = self.get_account_id()
        if not account_id:
            return []

        at = at or datetime.now()
        results: list[BrokerExecutionResult] = []
        max_passes = 20

        for _ in range(max_passes):
            snapshot = self.load_account_snapshot(account_id)
            if snapshot.account_state not in (
                AccountState.LIQUIDATION,
                AccountState.LIQUIDATION_PENDING,
            ):
                break

            rows = self.store.session.execute(
                text(
                    """
                    SELECT bp.id::text, bp.net_quantity, bp.mark_price, bp.maintenance_margin,
                           i.symbol, i.id::text AS instrument_id
                    FROM broker_positions bp
                    JOIN instruments i ON i.id = bp.instrument_id
                    WHERE bp.broker_account_id = :aid
                    ORDER BY bp.maintenance_margin DESC, i.symbol ASC, bp.id ASC
                    """
                ),
                {"aid": account_id},
            ).mappings().all()
            if not rows:
                self._set_account_state(account_id, AccountState.ACTIVE)
                break

            executed_this_pass = False
            deferred_closed_market = False

            for row in rows:
                instrument = self.store.get_instrument_by_symbol(row["symbol"])
                if not instrument:
                    continue

                market_open = market_open_for_instrument(instrument, at)
                if not market_open:
                    deferred_closed_market = True
                    continue

                from quantara_engine.execution.cost_profile import execution_assumptions_for
                from quantara_engine.execution.fill_calculator import FillResult as FR, calculate_fill_price

                qty = abs(Decimal(str(row["net_quantity"])))
                net = Decimal(str(row["net_quantity"]))
                close_dir = Direction.SHORT if net > 0 else Direction.LONG
                base = Decimal(str(row["mark_price"]))
                assumptions = execution_assumptions_for(instrument)
                fill_calc = calculate_fill_price(close_dir, "exit", base, qty, assumptions)

                intent = OrderIntent(
                    id=str(uuid.uuid4()),
                    signal_id="",
                    strategy_instance_id="",
                    portfolio_id="",
                    direction=close_dir,
                    quantity=qty,
                    stop_loss=Decimal("0"),
                    take_profit=None,
                    target_risk_amount=Decimal("0"),
                    actual_risk_amount=Decimal("0"),
                    signal_candle_timestamp=at,
                    risk_profile_id="",
                    status=IntentStatus.PENDING_EXECUTION,
                    is_close=True,
                )
                key = f"liquidation:{row['id']}:{at.isoformat()}"
                res = self.execute_order(
                    intent,
                    instrument,
                    fill_calc,
                    execution_at=at,
                    timeframe="5m",
                    idempotency_key=key,
                    order_purpose="liquidation",
                    is_liquidation=True,
                )
                results.append(res)
                if res.accepted:
                    executed_this_pass = True
                    break

            if deferred_closed_market and not executed_this_pass:
                self._set_account_state(account_id, AccountState.LIQUIDATION_PENDING)
                break
            if not executed_this_pass:
                break

            refreshed = self.load_account_snapshot(account_id)
            if refreshed.account_state not in (
                AccountState.LIQUIDATION,
                AccountState.LIQUIDATION_PENDING,
            ):
                self._set_account_state(account_id, refreshed.account_state)
                break

        return results

    # backward compat alias
    execute_intent = execute_order
