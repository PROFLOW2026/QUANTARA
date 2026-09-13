"""Full DB-backed simulated broker adapter — implements RealBrokerAdapter contract."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.broker_adapter_contract import (
    BrokerSubmitRequest,
    BrokerSubmitResult,
    ReconciliationReport,
)
from quantara_engine.broker.broker_authority import load_authority_order_by_client_id, persist_authority_order
from quantara_engine.broker.deterministic_fill_engine import plan_deterministic_fills
from quantara_engine.broker.execution_model import ExecutionModelVersion, uses_realistic_broker
from quantara_engine.broker.execution_product import EXECUTION_SIM_DEFAULTS, ExecutionProduct
from quantara_engine.broker.execution_timing import fill_timestamp_for_sequence, lifecycle_timestamps
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.broker.instrument_mapping import load_instrument_mapping
from quantara_engine.broker.live_execution_gate import assert_no_external_submission
from quantara_engine.broker.liquidation import evaluate_liquidation_state
from quantara_engine.broker.order_lifecycle import can_transition
from quantara_engine.broker.short_locate import LocateStatus, check_short_locate
from quantara_engine.broker.types import BrokerOrderStatus
from quantara_engine.domain.types import Direction
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.persistence.store import TradingStore


class SimulatedBrokerAdapter:
    """Authoritative simulated external broker — persisted in PostgreSQL."""

    def __init__(
        self,
        store: TradingStore,
        *,
        account_slug: str,
        account_id: str,
        execution_model: ExecutionModelVersion,
    ) -> None:
        self.store = store
        self.account_slug = account_slug
        self.account_id = account_id
        self.execution_model = execution_model
        self._halted = False

    def is_halted(self) -> bool:
        row = self.store.session.execute(
            text("SELECT reconciliation_halted FROM broker_accounts WHERE id = :id"),
            {"id": self.account_id},
        ).scalar()
        return bool(row) or self._halted

    def set_halted(self, halted: bool) -> None:
        self._halted = halted
        try:
            self.store.session.execute(
                text("UPDATE broker_accounts SET reconciliation_halted = :h WHERE id = :id"),
                {"id": self.account_id, "h": halted},
            )
        except Exception:
            self.store.session.rollback()

    def get_account(self):
        from quantara_engine.broker.execution_service import BrokerExecutionService

        return BrokerExecutionService(self.store, account_slug=self.account_slug).load_account_snapshot(
            self.account_id
        )

    def get_cash(self) -> Decimal:
        row = self.store.session.execute(
            text("SELECT cash FROM broker_accounts WHERE id = :id"), {"id": self.account_id}
        ).scalar()
        return Decimal(str(row or 0))

    def get_equity(self) -> Decimal:
        row = self.store.session.execute(
            text("SELECT equity FROM broker_accounts WHERE id = :id"), {"id": self.account_id}
        ).scalar()
        return Decimal(str(row or 0))

    def get_buying_power(self) -> Decimal:
        row = self.store.session.execute(
            text("SELECT available_margin FROM broker_accounts WHERE id = :id"), {"id": self.account_id}
        ).scalar()
        return Decimal(str(row or 0))

    def get_margin_state(self) -> dict:
        row = self.store.session.execute(
            text(
                """
                SELECT equity, initial_margin_used, maintenance_margin_required,
                       free_margin, available_margin, account_state::text
                FROM broker_accounts WHERE id = :id
                """
            ),
            {"id": self.account_id},
        ).mappings().first()
        return dict(row) if row else {}

    def get_positions(self) -> dict:
        rows = self.store.session.execute(
            text(
                """
                SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price,
                       bp.liquidation_price, bp.execution_product::text
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = :aid
                """
            ),
            {"aid": self.account_id},
        ).mappings().all()
        return {str(r["symbol"]).upper(): dict(r) for r in rows}

    def get_position(self, symbol: str):
        return self.get_positions().get(symbol.upper().replace("/", ""))

    def get_orders(self, *, open_only: bool = False) -> list:
        q = """
            SELECT id::text, client_order_id, status::text, direction, requested_quantity,
                   filled_quantity, submission_unknown
            FROM broker_orders WHERE broker_account_id = :aid
        """
        if open_only:
            q += " AND status IN ('submitted', 'partially_filled', 'accepted', 'validating')"
        rows = self.store.session.execute(text(q), {"aid": self.account_id}).mappings().all()
        return [dict(r) for r in rows]

    def get_order(self, broker_order_id: str):
        row = self.store.session.execute(
            text("SELECT id::text, status::text, client_order_id FROM broker_orders WHERE id = :id"),
            {"id": broker_order_id},
        ).mappings().first()
        return dict(row) if row else None

    def get_order_by_client_id(self, client_order_id: str):
        return load_authority_order_by_client_id(self.store.session, self.account_id, client_order_id)

    def get_fills(self, broker_order_id: str) -> list:
        rows = self.store.session.execute(
            text(
                """
                SELECT fill_sequence, fill_price, fill_quantity, fees, filled_at
                FROM broker_fills WHERE broker_order_id = :oid ORDER BY fill_sequence
                """
            ),
            {"oid": broker_order_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_instrument_capabilities(self, symbol: str) -> dict:
        from quantara_engine.broker.execution_product import route_execution_product

        mapping = load_instrument_mapping(self.store, symbol)
        short_route = route_execution_product(
            symbol, "short", execution_model=self.execution_model
        )
        return {
            "symbol": mapping.canonical_symbol,
            "execution_product": mapping.execution_product.value,
            "short_capable": short_route.short_capable,
            "long_product": mapping.execution_product.value,
            "short_product": short_route.product.value,
            "min_quantity": str(mapping.min_quantity),
            "min_notional": str(mapping.min_notional),
        }

    def get_instrument_details(self, symbol: str) -> dict:
        spec = get_instrument_spec(symbol)
        mapping = load_instrument_mapping(self.store, symbol)
        return {
            "symbol": spec.symbol,
            "asset_class": spec.asset_class,
            "tick_size": str(spec.tick_size),
            "quantity_step": str(spec.quantity_step),
            "broker_symbol": mapping.broker_symbol,
        }

    def get_short_availability(self, symbol: str) -> dict:
        locate = check_short_locate(symbol, account_slug=self.account_slug)
        return {
            "symbol": symbol.upper(),
            "status": locate.status.value,
            "borrow_fee_annual_pct": str(locate.borrow_fee_annual_pct),
            "detail": locate.detail,
        }

    def get_market_status(self, symbol: str) -> dict:
        from quantara_engine.broker.market_gate import market_open_for_instrument

        instrument = self.store.get_instrument_by_symbol(symbol)
        if not instrument:
            return {"open": False, "reason": "unknown_instrument"}
        from datetime import timezone

        now = datetime.now(timezone.utc)
        open_ = market_open_for_instrument(instrument, now)
        return {"open": open_, "symbol": symbol.upper()}

    def submit_order(
        self,
        request: BrokerSubmitRequest,
        *,
        pre_accepted: bool,
        broker_order_id: str,
        execution_at: datetime,
        simulate_lost_response: bool = False,
    ) -> BrokerSubmitResult:
        assert_no_external_submission()
        if self.is_halted() and not request.reduce_only:
            return BrokerSubmitResult(accepted=False, status="rejected")

        existing = load_authority_order_by_client_id(
            self.store.session, self.account_id, request.client_order_id
        )
        if existing:
            return self._result_from_authority(existing)

        if not pre_accepted:
            persist_authority_order(
                self.store.session,
                broker_account_id=self.account_id,
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status="rejected",
            )
            return BrokerSubmitResult(accepted=False, broker_order_id=broker_order_id, status="rejected")

        if (
            request.direction == "short"
            and request.execution_product == ExecutionProduct.EQUITY_MARGIN_SHORT
        ):
            locate = check_short_locate(
                request.symbol,
                account_slug=self.account_slug,
                execution_product=request.execution_product,
            )
            if locate.status != LocateStatus.AVAILABLE:
                persist_authority_order(
                    self.store.session,
                    broker_account_id=self.account_id,
                    broker_order_id=broker_order_id,
                    client_order_id=request.client_order_id,
                    status="rejected",
                    payload={"locate": locate.status.value, "detail": locate.detail},
                )
                return BrokerSubmitResult(accepted=False, broker_order_id=broker_order_id, status="rejected")

        accepted_at, first_fill_at = lifecycle_timestamps(execution_at)
        persist_authority_order(
            self.store.session,
            broker_account_id=self.account_id,
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            status="submitted",
            remaining_quantity=request.quantity,
            accepted_at=accepted_at,
        )

        if simulate_lost_response:
            persist_authority_order(
                self.store.session,
                broker_account_id=self.account_id,
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status="submitted",
                submission_unknown=True,
                accepted_at=accepted_at,
            )
            return BrokerSubmitResult(
                accepted=False,
                broker_order_id=broker_order_id,
                status="submitted",
                submission_unknown=True,
            )

        instrument = self.store.get_instrument_by_symbol(request.symbol)
        assumptions = execution_assumptions_for(instrument, request.mark_price) if instrument else None
        if assumptions is None:
            return BrokerSubmitResult(accepted=False, broker_order_id=broker_order_id, status="rejected")

        slices = self._plan_fills(request, assumptions, execution_at)
        return self._build_fill_result(broker_order_id, request, slices, execution_at, first_fill_at)

    def recover_submission(self, client_order_id: str) -> BrokerSubmitResult | None:
        auth = load_authority_order_by_client_id(self.store.session, self.account_id, client_order_id)
        if not auth:
            return None
        persist_authority_order(
            self.store.session,
            broker_account_id=self.account_id,
            broker_order_id=auth["broker_order_id"],
            client_order_id=client_order_id,
            status=str(auth["authoritative_status"]),
            filled_quantity=Decimal(str(auth.get("filled_quantity") or 0)),
            submission_unknown=False,
        )
        return self._result_from_authority(auth)

    def cancel_order(self, broker_order_id: str) -> bool:
        row = self.get_order(broker_order_id)
        if not row:
            return False
        status = str(row["status"])
        if status in ("filled", "cancelled", "rejected"):
            return False
        self.store.session.execute(
            text(
                """
                UPDATE broker_orders SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
                WHERE id = :id AND broker_account_id = :aid
                """
            ),
            {"id": broker_order_id, "aid": self.account_id},
        )
        auth = self.store.session.execute(
            text("SELECT client_order_id FROM broker_orders WHERE id = :id"),
            {"id": broker_order_id},
        ).scalar()
        if auth:
            persist_authority_order(
                self.store.session,
                broker_account_id=self.account_id,
                broker_order_id=broker_order_id,
                client_order_id=str(auth),
                status="cancelled",
            )
        return True

    def replace_order(self, broker_order_id: str, **updates) -> bool:
        row = self.store.session.execute(
            text(
                """
                SELECT id::text, status::text, client_order_id, requested_quantity,
                       filled_quantity, remaining_quantity
                FROM broker_orders WHERE id = :id AND broker_account_id = :aid
                """
            ),
            {"id": broker_order_id, "aid": self.account_id},
        ).mappings().first()
        if not row:
            return False
        if str(row["status"]) not in ("submitted", "partially_filled", "accepted"):
            return False

        new_qty = updates.get("quantity")
        remaining = Decimal(str(row.get("remaining_quantity") or row["requested_quantity"])) - Decimal(
            str(row.get("filled_quantity") or 0)
        )
        if new_qty is not None:
            nq = Decimal(str(new_qty))
            if nq <= 0 or nq > remaining:
                return False
            self.store.session.execute(
                text(
                    """
                    UPDATE broker_orders SET remaining_quantity = :rq, updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": broker_order_id, "rq": nq},
            )
        persist_authority_order(
            self.store.session,
            broker_account_id=self.account_id,
            broker_order_id=broker_order_id,
            client_order_id=str(row["client_order_id"]),
            status=str(row["status"]),
            filled_quantity=Decimal(str(row.get("filled_quantity") or 0)),
            remaining_quantity=Decimal(str(updates.get("quantity", remaining))),
            payload={"replaced": True, "updates": {k: str(v) for k, v in updates.items()}},
        )
        return True

    def sync_reconcile(self) -> ReconciliationReport:
        from quantara_engine.broker.reconciliation_loop import run_broker_reconciliation

        return run_broker_reconciliation(
            self.store,
            account_id=self.account_id,
            account_slug=self.account_slug,
            broker_engine=None,
        )

    def _plan_fills(self, request: BrokerSubmitRequest, assumptions, execution_at: datetime) -> list:
        direction = Direction.LONG if request.direction == "long" else Direction.SHORT
        side = "exit" if request.reduce_only else "entry"
        return plan_deterministic_fills(
            client_order_id=request.client_order_id,
            execution_ts_iso=execution_at.isoformat(),
            direction=direction,
            side=side,
            total_quantity=request.quantity,
            base_price=request.mark_price,
            assumptions=assumptions,
            allow_partial=uses_realistic_broker(self.execution_model),
        )

    def _build_fill_result(
        self, broker_order_id: str, request: BrokerSubmitRequest, slices, execution_at, first_fill_at
    ) -> BrokerSubmitResult:
        total = sum(s.quantity for s in slices)
        status = "filled" if total >= request.quantity else "partially_filled"
        persist_authority_order(
            self.store.session,
            broker_account_id=self.account_id,
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            status=status,
            filled_quantity=total,
            remaining_quantity=max(Decimal("0"), request.quantity - total),
            first_fill_at=first_fill_at,
            last_fill_at=fill_timestamp_for_sequence(execution_at, len(slices)),
        )
        return BrokerSubmitResult(
            accepted=True,
            broker_order_id=broker_order_id,
            status=status,
            fill_slices=slices,
        )

    def _result_from_authority(self, auth: dict) -> BrokerSubmitResult:
        status = str(auth["authoritative_status"])
        return BrokerSubmitResult(
            accepted=status in ("filled", "partially_filled", "submitted"),
            broker_order_id=auth["broker_order_id"],
            status=status,
            submission_unknown=bool(auth.get("submission_unknown")),
            fill_slices=[],
        )


def adapter_for(store: TradingStore, account_slug: str, account_id: str, execution_model: ExecutionModelVersion):
    return SimulatedBrokerAdapter(
        store, account_slug=account_slug, account_id=account_id, execution_model=execution_model
    )
