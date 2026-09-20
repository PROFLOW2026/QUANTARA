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
from quantara_engine.broker.client_order_id import derive_client_order_id
from quantara_engine.broker.execution_model import (
    ExecutionModelVersion,
    parse_execution_model,
    resolve_execution_model,
    uses_realistic_broker,
)
from quantara_engine.broker.execution_product import route_execution_product
from quantara_engine.broker.provenance import coerce_uuid
from quantara_engine.broker.realistic_execution import (
    aggregate_fill_result,
    insert_broker_order,
    submit_to_simulated_broker,
    update_order_status,
)
from quantara_engine.broker.cost_accrual import accrue_position_costs
from quantara_engine.broker.execution_timing import lifecycle_timestamps, fill_timestamp_for_sequence
from quantara_engine.broker.liquidation import liquidation_price
from quantara_engine.broker.protective_orders import (
    cancel_protective_orders_for_position,
    create_protective_orders_for_position,
    sync_protective_quantity,
)
from quantara_engine.broker.reconciliation_loop import run_broker_reconciliation
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
from quantara_engine.broker.accounts import PAPER_ACCOUNT_SLUG, RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates


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
    shadow_only: bool = False
    physical_opened_qty: Decimal = Decimal("0")
    physical_closed_qty: Decimal = Decimal("0")
    gross_realized_pnl: Decimal = Decimal("0")
    net_realized_pnl: Decimal = Decimal("0")


class BrokerExecutionService:
    """Persisted paper broker — NETTING mode only."""

    def __init__(self, store: TradingStore, *, account_slug: str | None = None) -> None:
        self.store = store
        self.account_slug = account_slug or PAPER_ACCOUNT_SLUG
        from quantara_engine.broker.profile import profile_for_account_slug

        self.profile = profile_for_account_slug(self.account_slug)

    def _tables_ready(self) -> bool:
        try:
            self.store.session.execute(text("SELECT 1 FROM broker_accounts LIMIT 1"))
            return True
        except Exception:
            return False

    def get_account_row(self) -> dict | None:
        if not self._tables_ready():
            return None
        try:
            return self.store.session.execute(
                text(
                    """
                    SELECT id::text, is_active, pending_owner_reset, account_state::text AS account_state,
                           cash, balance, realized_pnl, gross_realized_pnl, fees_paid, spot_crypto_cash,
                           execution_model::text AS execution_model,
                           reconciliation_halted
                    FROM broker_accounts WHERE slug = :slug
                    """
                ),
                {"slug": self.account_slug},
            ).mappings().first()
        except Exception:
            row = self.store.session.execute(
                text(
                    """
                    SELECT id::text, is_active, pending_owner_reset, account_state::text AS account_state,
                           cash, balance, realized_pnl, spot_crypto_cash
                    FROM broker_accounts WHERE slug = :slug
                    """
                ),
                {"slug": self.account_slug},
            ).mappings().first()
            if row:
                d = dict(row)
                d.setdefault("gross_realized_pnl", d.get("realized_pnl"))
                d.setdefault("fees_paid", Decimal("0"))
                d.setdefault("reconciliation_halted", False)
                return d
            return None

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

        from quantara_engine.broker.spot_crypto_cash import effective_spot_crypto_cash

        raw_cash = Decimal(str(row["cash"]))
        raw_spot = Decimal(str(row["spot_crypto_cash"]))
        snap = build_account_snapshot(
            cash=raw_cash,
            balance=Decimal(str(row["balance"])),
            realized_pnl=Decimal(str(row["realized_pnl"])),
            positions=positions,
            fx_rates=fx_map,
            profile=self.profile,
            spot_crypto_cash=effective_spot_crypto_cash(
                cash=raw_cash,
                spot_crypto_cash=raw_spot,
            ),
        )
        # row realized_pnl is NET; gross/fees available via get_account_row
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
        gross_realized_pnl: Decimal | None = None,
        fees_paid: Decimal | None = None,
    ) -> None:
        params = {
            "id": account_id,
            "cash": cash,
            "balance": balance,
            "realized": realized_pnl,
            "spot": spot_crypto_cash,
        }
        if gross_realized_pnl is not None and fees_paid is not None:
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_accounts SET
                      cash = :cash, balance = :balance, realized_pnl = :realized,
                      gross_realized_pnl = :gross, fees_paid = :fees,
                      spot_crypto_cash = :spot, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {**params, "gross": gross_realized_pnl, "fees": fees_paid},
            )
        else:
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_accounts SET
                      cash = :cash, balance = :balance, realized_pnl = :realized,
                      spot_crypto_cash = :spot, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                params,
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
                  equity = :equity,
                  unrealized_pnl = :unrealized,
                  gross_exposure = :gross, net_exposure = :net,
                  initial_margin_used = :im, maintenance_margin_required = :mm,
                  free_margin = :fm, available_margin = :am,
                  account_state = :state, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": account_id,
                "equity": snapshot.equity,
                "unrealized": snapshot.unrealized_pnl,
                "gross": snapshot.gross_exposure,
                "net": snapshot.net_exposure,
                "im": snapshot.initial_margin_used,
                "mm": snapshot.maintenance_margin_required,
                "fm": snapshot.free_margin,
                "am": snapshot.available_margin,
                "state": state,
            },
        )

    def _execution_model(self, account_row: dict | None = None) -> ExecutionModelVersion:
        row = account_row or self.get_account_row()
        if row and row.get("execution_model"):
            return parse_execution_model(str(row["execution_model"]))
        return resolve_execution_model(self.store, self.account_slug)

    def _load_existing_fill(
        self,
        account_id: str,
        idempotency_key: str,
        client_order_id: str | None = None,
    ) -> BrokerExecutionResult | None:
        row = self.store.session.execute(
            text(
                """
                SELECT o.id::text AS order_id, o.status::text AS status,
                       f.id::text AS fill_id, f.fill_price, f.fill_quantity,
                       f.realized_pnl, f.fees, f.spread_cost, f.slippage,
                       o.filled_quantity, o.submission_unknown
                FROM broker_orders o
                LEFT JOIN broker_fills f ON f.broker_order_id = o.id
                WHERE o.broker_account_id = :aid
                  AND (o.idempotency_key = :key OR (:cid IS NOT NULL AND o.client_order_id = :cid))
                ORDER BY f.fill_sequence DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"aid": account_id, "key": idempotency_key, "cid": client_order_id},
        ).mappings().first()
        if not row:
            return None
        if row["status"] in ("filled", "partially_filled") and row["fill_id"]:
            qty = row.get("filled_quantity") or row["fill_quantity"]
            opened = self.store.session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(l.remaining_qty), 0)
                    FROM broker_attribution_lots l
                    WHERE l.broker_fill_id = CAST(:fid AS uuid)
                    """
                ),
                {"fid": row["fill_id"]},
            ).scalar()
            return BrokerExecutionResult(
                accepted=True,
                broker_order_id=row["order_id"],
                broker_fill_id=row["fill_id"],
                fill_price=Decimal(str(row["fill_price"])),
                fill_quantity=Decimal(str(qty)),
                realized_pnl=Decimal(str(row["realized_pnl"])),
                fees=Decimal(str(row["fees"])),
                spread_cost=Decimal(str(row["spread_cost"] or 0)),
                slippage=Decimal(str(row["slippage"] or 0)),
                physical_opened_qty=Decimal(str(opened or 0)),
                from_existing_fill=True,
            )
        if row["status"] == "rejected":
            return BrokerExecutionResult(accepted=False, broker_order_id=row["order_id"])
        if row.get("submission_unknown"):
            return BrokerExecutionResult(accepted=False, broker_order_id=row["order_id"])
        return None

    def _execution_fees(
        self,
        fill: FillResult,
        *,
        instrument: Instrument | None = None,
        execution_product: str | None = None,
        quantity: Decimal | None = None,
    ) -> Decimal:
        """Explicit commission only — spread/slippage are embedded in fill price."""
        if instrument and execution_product and quantity is not None:
            try:
                from quantara_engine.broker.execution_product import ExecutionProduct
                from quantara_engine.broker.fees import calculate_commission, load_fee_profile
                from quantara_engine.broker.vendor import parse_vendor

                row = self.get_account_row() or {}
                vendor_row = self.store.session.execute(
                    text("SELECT broker_vendor::text FROM broker_accounts WHERE slug = :slug"),
                    {"slug": self.account_slug},
                ).scalar()
                vendor = parse_vendor(str(vendor_row) if vendor_row else "SIMULATED")
                fee_model = load_fee_profile(
                    self.store,
                    broker_vendor=vendor,
                    execution_product=ExecutionProduct(execution_product),
                )
                if fee_model:
                    notional = quantity * fill.fill_price
                    return calculate_commission(
                        fee_model=fee_model, notional=notional, quantity=quantity
                    ).commission
            except Exception:
                pass
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
        strategy_intent_id: str | None = None,
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

        from quantara_engine.live_sim.execution_routing import assert_live_sim_execution_target_allowed

        allowed_target, block_reason = assert_live_sim_execution_target_allowed(
            self.store, self.account_slug
        )
        if not allowed_target:
            return BrokerExecutionResult(
                accepted=False,
                decision=BrokerOrderDecision(
                    accepted=False,
                    accepted_quantity=Decimal("0"),
                    rejection_reason=BrokerRejectionReason.ACCOUNT_PAUSED,
                    rejection_detail=block_reason,
                ),
            )

        account_id = account_row["id"]
        if account_row.get("reconciliation_halted"):
            return BrokerExecutionResult(
                accepted=False,
                decision=BrokerOrderDecision(
                    accepted=False,
                    accepted_quantity=Decimal("0"),
                    rejection_reason=BrokerRejectionReason.RECONCILIATION_HALTED,
                    rejection_detail="reconciliation halted — resolve before new execution",
                ),
            )

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

        execution_model = self._execution_model(account_row)
        client_order_id = derive_client_order_id(
            account_slug=self.account_slug,
            idempotency_key=idempotency_key,
        )
        existing = self._load_existing_fill(account_id, idempotency_key, client_order_id)
        if existing:
            return existing

        market_open = market_open_for_instrument(instrument, execution_at)
        data_fresh, _ = data_fresh_for_instrument(self.store, instrument, timeframe, execution_at)
        snapshot = self.load_account_snapshot(account_id)
        # FX must cover every open broker position, not only the traded symbol —
        # pre_trade projects the full book when evaluating closes.
        fx_instruments = [instrument]
        for sym in snapshot.positions:
            other = self.store.get_instrument_by_symbol(sym)
            if other is not None:
                fx_instruments.append(other)
        fx = resolve_dashboard_fx_rates(
            self.store, quote_currencies_for_instruments(fx_instruments)
        )
        fx_map = {k: v for k, v in fx.quote_per_usd.items()}
        if "JPY" not in fx_map and any(
            getattr(i, "symbol", "").upper().endswith("JPY") for i in fx_instruments
        ):
            # Dashboard cache may omit JPY; fall back to live candle resolver.
            from quantara_engine.portfolio.currency import resolve_fx_rates

            live_fx = resolve_fx_rates(self.store, {"USD", "JPY"})
            fx_map.update({k: v for k, v in live_fx.quote_per_usd.items()})

        direction = "long" if intent.direction == Direction.LONG else "short"
        is_close = intent.is_close or order_purpose in ("sl", "tp", "close", "flatten", "liquidation")
        route = route_execution_product(
            instrument.symbol,
            direction,
            execution_model=execution_model,
            is_close=is_close,
        )
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
            execution_product=route.product.value,
            product_rules_key=route.asset_class_key,
            account_slug=self.account_slug,
        )
        decision = evaluate_broker_order(
            snapshot, self.profile, request, fx_map, product_rules=route.rules
        )

        order_id = str(uuid.uuid4())
        db_strategy_intent_id = None if is_liquidation else coerce_uuid(strategy_intent_id)
        db_strategy_portfolio_id = None if is_liquidation else coerce_uuid(intent.portfolio_id)
        try:
            if uses_realistic_broker(execution_model):
                insert_broker_order(
                    self.store.session,
                    order_id=order_id,
                    account_id=account_id,
                    instrument_id=instrument.id,
                    direction=direction,
                    quantity=intent.quantity,
                    idempotency_key=idempotency_key,
                    client_order_id=client_order_id,
                    execution_product=route.product.value,
                    execution_model=execution_model.value,
                    order_purpose=order_purpose,
                    execution_at=execution_at,
                    db_strategy_intent_id=db_strategy_intent_id,
                    db_strategy_portfolio_id=db_strategy_portfolio_id,
                )
            else:
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
                        "iid": db_strategy_intent_id,
                        "pid": db_strategy_portfolio_id,
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
            dup = self._load_existing_fill(account_id, idempotency_key, client_order_id)
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
                    "pid": db_strategy_portfolio_id,
                    "sym": instrument.symbol,
                    "qty": intent.quantity,
                    "reason": reason,
                    "detail": decision.rejection_detail,
                    "opp": opportunity_key,
                },
            )
            self.store.session.flush()
            return BrokerExecutionResult(accepted=False, broker_order_id=order_id, decision=decision)

        if uses_realistic_broker(execution_model):
            accepted_at, first_fill_at = lifecycle_timestamps(execution_at)
            update_order_status(
                self.store.session,
                order_id,
                "accepted",
                accepted_at=accepted_at,
            )
            update_order_status(self.store.session, order_id, "submitted")
            _, sim_result = submit_to_simulated_broker(
                self.store,
                account_slug=self.account_slug,
                account_id=account_id,
                execution_model=execution_model,
                broker_order_id=order_id,
                client_order_id=client_order_id,
                symbol=instrument.symbol,
                direction=direction,
                quantity=decision.accepted_quantity,
                mark_price=fill.fill_price,
                route=route,
                pre_accepted=True,
                execution_at=execution_at,
                instrument=instrument,
                is_close=is_close,
            )
            if sim_result.submission_unknown:
                update_order_status(
                    self.store.session,
                    order_id,
                    "submitted",
                    submission_unknown=True,
                )
                self.store.session.flush()
                return BrokerExecutionResult(accepted=False, broker_order_id=order_id, decision=decision)
            if not sim_result.accepted or not sim_result.fill_slices:
                update_order_status(self.store.session, order_id, "rejected")
                self.store.session.flush()
                return BrokerExecutionResult(accepted=False, broker_order_id=order_id, decision=decision)

            combined = aggregate_fill_result(sim_result.fill_slices)
            total_filled = sum(s.quantity for s in sim_result.fill_slices)
            remaining = decision.accepted_quantity - total_filled
            update_order_status(
                self.store.session,
                order_id,
                sim_result.status,
                filled_quantity=total_filled,
                remaining_quantity=max(Decimal("0"), remaining),
                filled_at=first_fill_at if sim_result.status == "filled" else None,
            )
            result = self._apply_fill_slices(
                account_id=account_id,
                order_id=order_id,
                instrument=instrument,
                direction=direction,
                slices=sim_result.fill_slices,
                execution_at=first_fill_at,
                fx_map=fx_map,
                snapshot=snapshot,
                portfolio_id=intent.portfolio_id,
                strategy_position_id=strategy_position_id,
                opportunity_key=opportunity_key,
                is_close=is_close,
                order_purpose=order_purpose,
                product_rules=route.rules,
                execution_product=route.product.value,
                stop_loss=intent.stop_loss if intent.stop_loss and intent.stop_loss > 0 else None,
                take_profit=intent.take_profit,
            )
            run_broker_reconciliation(
                self.store,
                account_id=account_id,
                account_slug=self.account_slug,
            )
            return result

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

    def _apply_fill_slices(
        self,
        *,
        account_id: str,
        order_id: str,
        instrument: Instrument,
        direction: str,
        slices,
        execution_at: datetime,
        fx_map: dict[str, Decimal],
        snapshot,
        portfolio_id: str,
        strategy_position_id: str | None,
        opportunity_key: str | None,
        is_close: bool,
        order_purpose: str,
        product_rules=None,
        execution_product: str | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> BrokerExecutionResult:
        last_result: BrokerExecutionResult | None = None
        for sl in slices:
            last_result = self._apply_fill(
                account_id=account_id,
                order_id=order_id,
                instrument=instrument,
                direction=direction,
                quantity=sl.quantity,
                fill=sl.fill,
                execution_at=execution_at,
                fx_map=fx_map,
                snapshot=snapshot,
                portfolio_id=portfolio_id,
                strategy_position_id=strategy_position_id,
                opportunity_key=opportunity_key,
                is_close=is_close,
                order_purpose=order_purpose,
                fill_sequence=sl.sequence,
                product_rules=product_rules,
                execution_product=execution_product,
                defer_order_status=True,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            snapshot = self.load_account_snapshot(account_id)
        return last_result or BrokerExecutionResult(accepted=False)

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
        fill_sequence: int = 1,
        product_rules=None,
        execution_product: str | None = None,
        defer_order_status: bool = False,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> BrokerExecutionResult:
        spec = get_instrument_spec(instrument.symbol)
        rules = product_rules or self.profile.rules_for(spec.asset_class)
        fees = self._execution_fees(fill)

        existing_identical = self.store.session.execute(
            text(
                """
                SELECT id::text AS fill_id, realized_pnl, fees, spread_cost, slippage
                FROM broker_fills
                WHERE broker_order_id = :oid
                  AND fill_quantity = :qty
                  AND fill_price = :price
                  AND filled_at = :at
                ORDER BY fill_sequence ASC
                LIMIT 1
                """
            ),
            {
                "oid": order_id,
                "qty": quantity,
                "price": fill.fill_price,
                "at": execution_at,
            },
        ).mappings().first()
        if existing_identical:
            opened = self.store.session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(l.remaining_qty), 0)
                    FROM broker_attribution_lots l
                    WHERE l.broker_fill_id = CAST(:fid AS uuid)
                    """
                ),
                {"fid": existing_identical["fill_id"]},
            ).scalar()
            net_pnl = Decimal(str(existing_identical["realized_pnl"])) - Decimal(
                str(existing_identical["fees"] or 0)
            )
            return BrokerExecutionResult(
                accepted=True,
                broker_order_id=order_id,
                broker_fill_id=existing_identical["fill_id"],
                fill_price=fill.fill_price,
                fill_quantity=quantity,
                realized_pnl=net_pnl,
                gross_realized_pnl=Decimal(str(existing_identical["realized_pnl"])),
                net_realized_pnl=net_pnl,
                fees=Decimal(str(existing_identical["fees"] or 0)),
                spread_cost=Decimal(str(existing_identical["spread_cost"] or 0)),
                slippage=Decimal(str(existing_identical["slippage"] or 0)),
                physical_opened_qty=Decimal(str(opened or 0)),
                from_existing_fill=True,
            )

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

        gross_fill_pnl = netting.realized_pnl
        row = self.get_account_row() or {}
        prev_gross = Decimal(str(row.get("gross_realized_pnl") if row.get("gross_realized_pnl") is not None else "0"))
        prev_fees = Decimal(str(row.get("fees_paid") if row.get("fees_paid") is not None else "0"))
        new_gross = prev_gross + gross_fill_pnl
        new_fees = prev_fees + fees
        new_realized = new_gross - new_fees
        new_balance = snapshot.balance + gross_fill_pnl - fees
        net_fill_pnl = gross_fill_pnl - fees
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
            new_cash += gross_fill_pnl - fees

        fill_id = str(uuid.uuid4())
        position_id = pos_row["id"] if pos_row else str(uuid.uuid4())

        pos_dir = "long" if netting.new_net_qty > 0 else "short"
        liq_px = liquidation_price(
            direction=pos_dir,
            average_price=netting.new_avg_price,
            rules=rules,
        )

        if netting.new_net_qty == 0:
            if pos_row:
                cancel_protective_orders_for_position(
                    self.store, broker_position_id=position_id
                )
                self.store.session.execute(
                    text(
                        "UPDATE broker_fills SET broker_position_id = NULL WHERE broker_position_id = :id"
                    ),
                    {"id": position_id},
                )
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
                      initial_margin = :im, maintenance_margin = :mm,
                      liquidation_price = :liq, updated_at = NOW()
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
                    "liq": liq_px,
                },
            )
            if is_close:
                cancel_protective_orders_for_position(
                    self.store, broker_position_id=position_id
                )
            elif not is_close:
                sync_protective_quantity(
                    self.store,
                    broker_position_id=position_id,
                    quantity=abs(netting.new_net_qty),
                )
        else:
            notional = quote_notional_usd(netting.new_net_qty, fill.fill_price, spec, fx_map)
            unreal = unrealized_pnl_usd(netting.new_net_qty, netting.new_avg_price, fill.fill_price, spec, fx_map)
            self.store.session.execute(
                text(
                    """
                    INSERT INTO broker_positions (
                      id, broker_account_id, instrument_id, net_quantity, average_price,
                      mark_price, unrealized_pnl, initial_margin, maintenance_margin,
                      liquidation_price
                    ) VALUES (:id, :aid, :iid, :qty, :avg, :mark, :upnl, :im, :mm, :liq)
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
                    "liq": liq_px,
                },
            )
            if not is_close and (stop_loss or take_profit):
                create_protective_orders_for_position(
                    self.store,
                    broker_account_id=account_id,
                    broker_position_id=position_id,
                    symbol=instrument.symbol,
                    direction=pos_dir,
                    quantity=abs(netting.new_net_qty),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    account_slug=self.account_slug,
                    opportunity_key=opportunity_key,
                )

        if not defer_order_status:
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
                ) VALUES (:id, :oid, :pid, :seq, :price, :qty, :pnl, :fees, :slip, :spread, :at)
                """
            ),
            {
                "id": fill_id,
                "oid": order_id,
                "pid": position_id,
                "seq": fill_sequence,
                "price": fill.fill_price,
                "qty": quantity,
                "pnl": gross_fill_pnl,
                "fees": fees,
                "slip": fill.slippage,
                "spread": fill.spread_cost,
                "at": execution_at,
            },
        )

        if execution_product and position_id and fill_sequence == 1 and not is_close:
            try:
                self.store.session.execute(
                    text(
                        """
                        UPDATE broker_positions SET execution_product = CAST(:product AS execution_product)
                        WHERE id = :id
                        """
                    ),
                    {"id": position_id, "product": execution_product},
                )
            except Exception:
                pass

        allocation = allocate_fill_to_strategy_legs(
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
            order_purpose=order_purpose,
        )

        self._persist_ledger_balances(
            account_id,
            cash=new_cash,
            balance=new_balance,
            realized_pnl=new_realized,
            spot_crypto_cash=new_spot,
            gross_realized_pnl=new_gross,
            fees_paid=new_fees,
        )
        self._maybe_attribute_equal_asset_fill(
            symbol=instrument.symbol,
            realized_pnl_delta=net_fill_pnl,
            fee_delta=fees,
        )
        self._refresh_account_from_db(account_id)
        self.store.session.flush()

        return BrokerExecutionResult(
            accepted=True,
            broker_order_id=order_id,
            broker_fill_id=fill_id,
            fill_price=fill.fill_price,
            fill_quantity=quantity,
            realized_pnl=net_fill_pnl,
            gross_realized_pnl=gross_fill_pnl,
            net_realized_pnl=net_fill_pnl,
            fees=fees,
            spread_cost=fill.spread_cost,
            slippage=fill.slippage,
            physical_opened_qty=allocation.physical_opened_qty,
            physical_closed_qty=allocation.physical_closed_qty,
        )

    def _refresh_account_from_db(self, account_id: str) -> None:
        row = self.get_account_row()
        snapshot = self.load_account_snapshot(account_id)
        self._persist_account_metrics(account_id, snapshot, row=row)
        try:
            from quantara_engine.live_sim.risk_policy import update_high_water_mark

            if row:
                update_high_water_mark(
                    self.store,
                    account_id,
                    Decimal(str(row.get("equity") or row.get("starting_cash") or 0)),
                )
        except Exception:
            pass

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
                    SELECT bp.id::text AS id, bp.net_quantity, bp.average_price,
                           bp.execution_product::text AS execution_product
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
            try:
                accrue_position_costs(
                    self.store,
                    broker_account_id=account_id,
                    broker_position_id=row["id"],
                    symbol=symbol.upper(),
                    net_quantity=qty,
                    mark_price=mark,
                    execution_product=row.get("execution_product"),
                    fx_rates=fx_map,
                    now=at,
                )
            except Exception:
                pass

        self._refresh_account_from_db(account_id)
        self.run_liquidation_if_required(at=at)
        try:
            from quantara_engine.broker.accounts import (
                LIVE_SIM_IBKR_LIKE_SLUG,
                LIVE_SIM_KRAKEN_LIKE_SLUG,
                LIVE_SIM_10K_ACCOUNT_SLUG,
            )
            from quantara_engine.owner_portfolio.asset_ledger import (
                refresh_all_asset_states_from_positions,
            )
            from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

            if self.account_slug in {
                LIVE_SIM_IBKR_LIKE_SLUG,
                LIVE_SIM_KRAKEN_LIKE_SLUG,
                LIVE_SIM_10K_ACCOUNT_SLUG,
            }:
                refresh_all_asset_states_from_positions(self.store, LIVE_SIM_OWNER_SLUG)
        except Exception:
            pass

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

    def _maybe_attribute_equal_asset_fill(
        self,
        *,
        symbol: str,
        realized_pnl_delta: Decimal,
        fee_delta: Decimal,
    ) -> None:
        from quantara_engine.owner_portfolio.asset_allocation import is_equal_asset_mode_active
        from quantara_engine.owner_portfolio.asset_ledger import apply_asset_fill_impact
        from quantara_engine.owner_portfolio.service import IBKR_LIKE_SLUG, KRAKEN_LIKE_SLUG, LIVE_SIM_OWNER_SLUG

        if self.account_slug not in (IBKR_LIKE_SLUG, KRAKEN_LIKE_SLUG):
            return
        if not is_equal_asset_mode_active(self.store, LIVE_SIM_OWNER_SLUG):
            return
        apply_asset_fill_impact(
            self.store,
            owner_slug=LIVE_SIM_OWNER_SLUG,
            canonical_symbol=symbol,
            realized_pnl_delta=realized_pnl_delta,
            fee_delta=fee_delta,
            increment_trade_count=True,
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
                    strategy_intent_id=None,
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
