"""PostgreSQL adapter implementing OptionsSimulationEngine's `OptionsSimRepository` port.

Translates atomically between `SimAccountState` / `SimStrategy` / `SimLeg` /
`SimStockLot` / `SimJournalEvent` and the `opt_*` PostgreSQL tables without
touching any equity or System A/B structures.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from quantara_options.contracts import (
    ALL_9_STRATEGY_LABELS_HE,
    AccountRuleProfile,
    DecisionMode,
    ExerciseStyle,
    Greeks,
    LegPlan,
    LegRole,
    OptionContractSpec,
    OptionRight,
    OptionSide,
    StrategyStatus,
    StrategyType,
)
from quantara_options.db.repository import CLOSED_STATUSES, OptionsRepository
from quantara_options.math_pricing import compute_payoff_curve
from quantara_options.sim.accounting import (
    LegStatus,
    SimAccountState,
    SimJournalEvent,
    SimLeg,
    SimStockLot,
    SimStrategy,
    compute_account_totals,
    to_account_summary,
)
from quantara_options.sim.fees_and_slippage import ZERO, money
from quantara_options.sim.lifecycle import calendar_dte

_OPT_UUID_NS = uuid.UUID("a4b8f190-5c72-4a2b-9f0e-202611210001")


def _as_uuid(raw_id: str) -> str:
    try:
        return str(uuid.UUID(str(raw_id)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(_OPT_UUID_NS, str(raw_id)))


def _d(v: Any, default: Decimal = ZERO) -> Decimal:
    if v is None:
        return default
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _jload(v: Any, default: Any) -> Any:
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _jdumps(v: Any) -> str:
    def _default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        if hasattr(o, "value"):
            return o.value
        return str(o)

    return json.dumps(v, default=_default)


class PostgresOptionsSimAdapter:
    """Implements `OptionsSimRepository` on top of `OptionsRepository` + SQLAlchemy Session."""

    def __init__(self, repo: OptionsRepository) -> None:
        self.repo = repo
        self.session: Session = repo.session

    def _exec(self, sql: str, params: Optional[Mapping[str, Any]] = None):
        return self.session.execute(text(sql), dict(params or {}))

    # ------------------------------------------------------------------ loads

    def load_account_state(self, account_slug: str) -> Optional[SimAccountState]:
        acc = self._exec(
            "SELECT * FROM opt_accounts WHERE slug = :slug",
            {"slug": account_slug},
        ).mappings().first()
        if acc is None:
            return None

        profile_raw = _jload(acc.get("rule_profile"), {}) or {}
        try:
            profile = AccountRuleProfile(**profile_raw)
        except Exception:
            profile = AccountRuleProfile()
        wheel_states = {str(k): str(v) for k, v in (_jload(acc.get("wheel_states"), {}) or {}).items()}
        rmeta = _jload(acc.get("runtime_meta"), {}) or {}

        lot_rows = self._exec(
            "SELECT * FROM opt_stock_lots WHERE account_slug = :slug ORDER BY underlying_symbol",
            {"slug": account_slug},
        ).mappings().all()
        lot_meta_map = rmeta.get("lot_meta") or {}
        lots: list[SimStockLot] = []
        for lr in lot_rows:
            shares = int(lr["shares"] or 0)
            if shares <= 0:
                continue
            sym = str(lr["underlying_symbol"]).upper()
            lmeta = lot_meta_map.get(sym) or {}
            acquired_raw = lmeta.get("acquired_at")
            try:
                acquired_at = datetime.fromisoformat(acquired_raw) if acquired_raw else (lr["updated_at"] or datetime.now(timezone.utc))
            except Exception:
                acquired_at = lr["updated_at"] or datetime.now(timezone.utc)
            lots.append(
                SimStockLot(
                    lot_id=str(lr["id"]),
                    account_slug=account_slug,
                    symbol=sym,
                    shares=shares,
                    cost_basis_per_share=_d(lr["avg_cost_basis"]),
                    premium_credit_per_share=_d(lmeta.get("premium_credit_per_share"), ZERO),
                    encumbered_shares=int(lr["encumbered_shares"] or 0),
                    last_price=_d(lmeta["last_price"]) if lmeta.get("last_price") is not None else None,
                    acquired_at=acquired_at,
                    source=str(lmeta.get("source") or "assignment"),
                    source_strategy_id=str(lr["source_strategy_id"]) if lr.get("source_strategy_id") else None,
                )
            )

        closed_stats = self._exec(
            """
            SELECT
              COUNT(*) AS closed_count,
              COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
              COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses,
              COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0), 0) AS gross_profit,
              COALESCE(SUM(-realized_pnl) FILTER (WHERE realized_pnl < 0), 0) AS gross_loss
            FROM opt_strategies
            WHERE account_slug = :slug AND status = ANY(:closed)
            """,
            {"slug": account_slug, "closed": list(CLOSED_STATUSES)},
        ).mappings().first()

        day_trades: list[date] = []
        for raw_d in rmeta.get("day_trade_dates") or []:
            try:
                day_trades.append(date.fromisoformat(str(raw_d)))
            except Exception:
                continue
        last_spots = {
            str(k).upper(): _d(v)
            for k, v in (rmeta.get("last_spot_by_symbol") or {}).items()
            if v is not None
        }
        day_start_date = None
        if rmeta.get("day_start_date"):
            try:
                day_start_date = date.fromisoformat(str(rmeta["day_start_date"]))
            except Exception:
                day_start_date = None

        return SimAccountState(
            slug=acc["slug"],
            starting_capital=_d(acc["starting_capital"]),
            cash_balance=_d(acc["cash_balance"]),
            account_id=str(acc["id"]),
            name=acc["name"],
            mode=DecisionMode(acc["mode"]),
            strategy_scope=acc.get("strategy_scope"),
            cash_reserved=_d(acc["cash_reserved"]),
            fees_paid=_d(acc["fees_paid"]),
            realized_pnl=_d(acc["realized_pnl"]),
            premium_collected_gross=_d(acc["premium_collected_gross"]),
            premium_realized_net=_d(acc["premium_realized_net"]),
            peak_equity=_d(acc["peak_equity"], _d(acc["starting_capital"])),
            max_drawdown_usd=_d(acc["max_drawdown_usd"]),
            max_drawdown_pct=_d(acc["max_drawdown_pct"]),
            rule_profile=profile,
            wheel_states=wheel_states,
            stock_lots=lots,
            day_start_date=day_start_date,
            day_start_equity=_d(rmeta["day_start_equity"]) if rmeta.get("day_start_equity") is not None else None,
            day_trade_dates=day_trades,
            last_spot_by_symbol=last_spots,
            closed_strategies_count=int(closed_stats["closed_count"]) if closed_stats else 0,
            winning_strategies=int(closed_stats["wins"]) if closed_stats else 0,
            losing_strategies=int(closed_stats["losses"]) if closed_stats else 0,
            gross_profit=_d(closed_stats["gross_profit"]) if closed_stats else ZERO,
            gross_loss=_d(closed_stats["gross_loss"]) if closed_stats else ZERO,
            is_active=bool(acc["is_active"]),
            experiment_group=acc.get("experiment_group"),
            created_at=acc.get("created_at"),
            updated_at=acc.get("updated_at"),
        )

    def _hydrate_strategies(self, rows: Sequence[Mapping[str, Any]]) -> list[SimStrategy]:
        if not rows:
            return []
        ids = [str(r["id"]) for r in rows]
        leg_rows = self._exec(
            "SELECT * FROM opt_legs WHERE strategy_id = ANY(CAST(:ids AS uuid[])) ORDER BY strategy_id, id",
            {"ids": ids},
        ).mappings().all()
        legs_by_sid: dict[str, list[Mapping[str, Any]]] = {}
        for lr in leg_rows:
            legs_by_sid.setdefault(str(lr["strategy_id"]), []).append(lr)

        out: list[SimStrategy] = []
        for r in rows:
            sid = str(r["id"])
            meta = _jload(r.get("metadata"), {}) or {}
            leg_meta_map = meta.get("_leg_meta") or {}
            sim_legs: list[SimLeg] = []
            for lr in legs_by_sid.get(sid, []):
                lid = str(lr["id"])
                lmeta = leg_meta_map.get(lid) or {}
                sim_legs.append(
                    SimLeg(
                        leg_id=lid,
                        underlying_symbol=str(lr["underlying_symbol"]).upper(),
                        leg_role=LegRole(lr["leg_role"]),
                        side=OptionSide(lr["side"]),
                        quantity=int(lr["quantity"] or 1),
                        multiplier=int(lr["multiplier"] or 100),
                        entry_price=_d(lr["entry_price"]),
                        occ_symbol=lr["occ_symbol"],
                        right=OptionRight(lr["option_right"]) if lr.get("option_right") else None,
                        strike=_d(lr["strike"]) if lr.get("strike") is not None else None,
                        expiration_date=lr.get("expiration_date"),
                        exercise_style=ExerciseStyle(lmeta.get("exercise_style") or ExerciseStyle.AMERICAN.value),
                        current_mark=_d(lr["current_mark"]),
                        bid=_d(lr["bid"]) if lr.get("bid") is not None else None,
                        ask=_d(lr["ask"]) if lr.get("ask") is not None else None,
                        mark_is_estimated=bool(lmeta.get("mark_is_estimated", False)),
                        implied_volatility=lr.get("implied_volatility"),
                        entry_implied_volatility=lmeta.get("entry_implied_volatility"),
                        greeks=Greeks(
                            delta=lr.get("delta"),
                            gamma=lr.get("gamma"),
                            theta=lr.get("theta"),
                            vega=lr.get("vega"),
                            rho=lmeta.get("rho"),
                            computed_locally=bool(lmeta.get("computed_locally", False)),
                        ),
                        status=str(lr["status"]),
                        exit_price=_d(lmeta["exit_price"]) if lmeta.get("exit_price") is not None else None,
                        realized_pnl=_d(lr["realized_pnl"]),
                        lot_id=lmeta.get("lot_id"),
                    )
                )

            exp_date = r["expiration_date"] or (
                next((l.expiration_date for l in sim_legs if l.expiration_date), date.today())
            )
            clean_meta = {k: v for k, v in meta.items() if k != "_leg_meta"}
            enc_lots = {
                str(k): int(v)
                for k, v in (meta.get("_encumbered_lots") or {}).items()
            }
            out.append(
                SimStrategy(
                    strategy_id=sid,
                    account_slug=r["account_slug"],
                    strategy_type=StrategyType(r["strategy_type"]),
                    underlying_symbol=str(r["underlying_symbol"]).upper(),
                    contracts_qty=int(r["contracts_qty"] or 1),
                    expiration_date=exp_date,
                    is_credit=bool(r["is_credit"]),
                    legs=sim_legs,
                    mode=DecisionMode(r["mode"]),
                    status=StrategyStatus(r["status"]),
                    net_entry_cash_flow=_d(r["net_entry_cash_flow"]),
                    collateral_locked=_d(r["collateral_locked_usd"]),
                    margin_requirement=_d(meta.get("_margin_requirement"), _d(r["collateral_locked_usd"])),
                    encumbered_lots=enc_lots,
                    max_profit_usd=_d(r["max_profit_usd"]) if r.get("max_profit_usd") is not None else None,
                    max_loss_usd=_d(r["max_loss_usd"]),
                    option_realized_gross=_d(meta.get("_option_realized_gross"), ZERO),
                    stock_realized_gross=_d(meta.get("_stock_realized_gross"), ZERO),
                    option_fees=_d(meta.get("_option_fees"), _d(r["fees_paid"])),
                    stock_fees=_d(meta.get("_stock_fees"), ZERO),
                    opened_at=r["opened_at"],
                    closed_at=r.get("closed_at"),
                    close_reason=r.get("close_reason"),
                    ai_rationale=r.get("ai_rationale"),
                    candidate_id=meta.get("candidate_id"),
                    wheel_state=r.get("wheel_state"),
                    pin_risk_warning=bool(r["pin_risk_warning"]),
                    early_assignment_warning=bool(r["early_assignment_warning"]),
                    exit_breach=meta.get("_exit_breach"),
                    metadata=clean_meta,
                )
            )
        return out

    def list_open_strategies(self, account_slug: str) -> list[SimStrategy]:
        rows = self._exec(
            "SELECT * FROM opt_strategies WHERE account_slug = :slug AND status = 'open' ORDER BY opened_at",
            {"slug": account_slug},
        ).mappings().all()
        return self._hydrate_strategies(rows)

    def get_strategy(self, strategy_id: str) -> Optional[SimStrategy]:
        row = self._exec(
            """
            SELECT * FROM opt_strategies
            WHERE id = CAST(:uid AS uuid) OR metadata->>'sim_id' = :raw
            LIMIT 1
            """,
            {"uid": _as_uuid(strategy_id), "raw": str(strategy_id)},
        ).mappings().first()
        if row is None:
            return None
        return self._hydrate_strategies([row])[0]

    # ------------------------------------------------------------------ save

    def save_simulation_state(
        self,
        account: SimAccountState,
        strategies: Sequence[SimStrategy],
        events: Sequence[SimJournalEvent],
    ) -> None:
        acc_row = self._exec(
            "SELECT id FROM opt_accounts WHERE slug = :slug FOR UPDATE",
            {"slug": account.slug},
        ).mappings().first()
        if acc_row is None:
            raise KeyError(f"options account not found: {account.slug}")
        account_id = str(acc_row["id"])
        account.account_id = account_id

        # 1) Normalize IDs to Postgres UUIDs up-front (including lot_id / strategy_id links)
        lot_id_map: dict[str, str] = {}
        for lot in account.stock_lots:
            old_lid = lot.lot_id
            new_lid = _as_uuid(old_lid)
            lot_id_map[old_lid] = new_lid
            lot.lot_id = new_lid
            if lot.source_strategy_id:
                lot.source_strategy_id = _as_uuid(lot.source_strategy_id)

        strat_id_map: dict[str, str] = {}
        for st in strategies:
            old_sid = st.strategy_id
            new_sid = _as_uuid(old_sid)
            strat_id_map[old_sid] = new_sid
            st.strategy_id = new_sid
            if st.encumbered_lots:
                st.encumbered_lots = {
                    lot_id_map.get(k, _as_uuid(k)): int(v)
                    for k, v in st.encumbered_lots.items()
                }
            for leg in st.legs:
                leg.leg_id = _as_uuid(leg.leg_id)
                if leg.lot_id:
                    leg.lot_id = lot_id_map.get(leg.lot_id, _as_uuid(leg.lot_id))

        # 2) Upsert stock lots (and zero out symbols no longer held)
        lot_meta: dict[str, Any] = {}
        held_symbols: set[str] = set()
        for lot in account.stock_lots:
            sym = lot.symbol.upper()
            held_symbols.add(sym)
            lot_meta[sym] = {
                "premium_credit_per_share": str(lot.premium_credit_per_share),
                "last_price": str(lot.last_price) if lot.last_price is not None else None,
                "acquired_at": lot.acquired_at.isoformat(),
                "source": lot.source,
            }
            self._exec(
                """
                INSERT INTO opt_stock_lots (
                  id, account_id, account_slug, underlying_symbol, shares,
                  avg_cost_basis, encumbered_shares, source_strategy_id, updated_at
                ) VALUES (
                  CAST(:id AS uuid), CAST(:aid AS uuid), :slug, :sym, :shares,
                  :basis, :enc, CAST(:src AS uuid), NOW()
                )
                ON CONFLICT (account_slug, underlying_symbol) DO UPDATE SET
                  shares = EXCLUDED.shares,
                  avg_cost_basis = EXCLUDED.avg_cost_basis,
                  encumbered_shares = EXCLUDED.encumbered_shares,
                  source_strategy_id = COALESCE(EXCLUDED.source_strategy_id, opt_stock_lots.source_strategy_id),
                  updated_at = NOW()
                """,
                {
                    "id": lot.lot_id,
                    "aid": account_id,
                    "slug": account.slug,
                    "sym": sym,
                    "shares": int(lot.shares),
                    "basis": lot.cost_basis_per_share,
                    "enc": int(lot.encumbered_shares),
                    "src": lot.source_strategy_id,
                },
            )
        if held_symbols:
            self._exec(
                """
                UPDATE opt_stock_lots SET shares = 0, encumbered_shares = 0, avg_cost_basis = 0, updated_at = NOW()
                WHERE account_slug = :slug AND NOT (underlying_symbol = ANY(:syms)) AND shares > 0
                """,
                {"slug": account.slug, "syms": list(held_symbols)},
            )
        else:
            self._exec(
                """
                UPDATE opt_stock_lots SET shares = 0, encumbered_shares = 0, avg_cost_basis = 0, updated_at = NOW()
                WHERE account_slug = :slug AND shares > 0
                """,
                {"slug": account.slug},
            )

        # 3) Upsert strategies + legs + contracts + audit order/fill if new
        for st in strategies:
            existing = self._exec(
                "SELECT id FROM opt_strategies WHERE id = CAST(:id AS uuid)",
                {"id": st.strategy_id},
            ).first()
            is_new = existing is None

            leg_meta: dict[str, Any] = {}
            net_delta = net_theta = net_vega = 0.0
            for leg in st.legs:
                leg_meta[leg.leg_id] = {
                    "exercise_style": leg.exercise_style.value,
                    "mark_is_estimated": leg.mark_is_estimated,
                    "entry_implied_volatility": leg.entry_implied_volatility,
                    "rho": leg.greeks.rho,
                    "computed_locally": leg.greeks.computed_locally,
                    "exit_price": str(leg.exit_price) if leg.exit_price is not None else None,
                    "lot_id": leg.lot_id,
                }
                if leg.is_option and leg.is_open:
                    scale = leg.multiplier * leg.quantity * leg.sign
                    net_delta += (leg.greeks.delta or 0.0) * scale
                    net_theta += (leg.greeks.theta or 0.0) * scale
                    net_vega += (leg.greeks.vega or 0.0) * scale
            if st.is_open and st.encumbered_lots:
                net_delta += sum(st.encumbered_lots.values())

            payoff_pts: list[dict[str, Any]] = []
            if st.is_open:
                spot = account.last_spot_by_symbol.get(st.underlying_symbol) or (
                    Decimal(str(st.metadata["entry_spot"])) if st.metadata.get("entry_spot") else None
                )
                if spot:
                    plans = []
                    for leg in st.legs:
                        if not leg.is_open:
                            continue
                        if leg.is_option:
                            plans.append(
                                LegPlan(
                                    occ_symbol=leg.occ_symbol,
                                    underlying_symbol=leg.underlying_symbol,
                                    leg_role=leg.leg_role,
                                    side=leg.side,
                                    right=leg.right,
                                    strike=leg.strike,
                                    expiration_date=leg.expiration_date,
                                    multiplier=leg.multiplier,
                                    ratio=max(1, leg.quantity // max(1, st.contracts_qty)),
                                    implied_volatility=leg.implied_volatility,
                                )
                            )
                        else:
                            plans.append(
                                LegPlan(
                                    underlying_symbol=leg.underlying_symbol,
                                    leg_role=leg.leg_role,
                                    side=leg.side,
                                    multiplier=max(1, leg.quantity // max(1, st.contracts_qty)),
                                    mid=leg.entry_price,
                                )
                            )
                    payoff_pts = [
                        p.model_dump(mode="json")
                        for p in compute_payoff_curve(
                            legs=plans,
                            spot=float(spot),
                            entry_option_cash_flow_usd=float(st.net_entry_cash_flow),
                            contracts_qty=st.contracts_qty,
                            dte_remaining=max(1, calendar_dte(st.expiration_date, account.updated_at)),
                        )
                    ]

            meta_payload = dict(st.metadata or {})
            if st.candidate_id:
                meta_payload["candidate_id"] = st.candidate_id
            if st.exit_breach:
                meta_payload["_exit_breach"] = st.exit_breach
            meta_payload["_encumbered_lots"] = st.encumbered_lots
            meta_payload["_margin_requirement"] = str(st.margin_requirement)
            meta_payload["_option_realized_gross"] = str(st.option_realized_gross)
            meta_payload["_stock_realized_gross"] = str(st.stock_realized_gross)
            meta_payload["_option_fees"] = str(st.option_fees)
            meta_payload["_stock_fees"] = str(st.stock_fees)
            meta_payload["_leg_meta"] = leg_meta

            unrealized = money(st.option_unrealized() - st.option_fees) if st.is_open else ZERO
            label_he = ALL_9_STRATEGY_LABELS_HE.get(st.strategy_type.value, st.strategy_type.value)

            self._exec(
                """
                INSERT INTO opt_strategies (
                  id, account_id, account_slug, strategy_type, strategy_label_he, underlying_symbol,
                  status, mode, contracts_qty, expiration_date, is_credit, net_entry_cash_flow,
                  open_liability_usd, long_value_usd, collateral_locked_usd, max_profit_usd,
                  max_loss_usd, unrealized_pnl, realized_pnl, fees_paid, net_delta, net_theta,
                  net_vega, pin_risk_warning, early_assignment_warning, wheel_state, ai_rationale,
                  close_reason, payoff_curve, metadata, opened_at, closed_at, updated_at
                ) VALUES (
                  CAST(:id AS uuid), CAST(:aid AS uuid), :slug, :stype, :label, :sym,
                  :status, :mode, :qty, :exp, :is_credit, :net_cash,
                  :liab, :lval, :coll, :maxp,
                  :maxl, :unreal, :real, :fees, :nd, :nt,
                  :nv, :pin, :early, :wheel, :rationale,
                  :reason, CAST(:curve AS jsonb), CAST(:meta AS jsonb), :opened, :closed, NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                  status = EXCLUDED.status,
                  open_liability_usd = EXCLUDED.open_liability_usd,
                  long_value_usd = EXCLUDED.long_value_usd,
                  collateral_locked_usd = EXCLUDED.collateral_locked_usd,
                  unrealized_pnl = EXCLUDED.unrealized_pnl,
                  realized_pnl = EXCLUDED.realized_pnl,
                  fees_paid = EXCLUDED.fees_paid,
                  net_delta = EXCLUDED.net_delta,
                  net_theta = EXCLUDED.net_theta,
                  net_vega = EXCLUDED.net_vega,
                  pin_risk_warning = EXCLUDED.pin_risk_warning,
                  early_assignment_warning = EXCLUDED.early_assignment_warning,
                  wheel_state = EXCLUDED.wheel_state,
                  ai_rationale = EXCLUDED.ai_rationale,
                  close_reason = EXCLUDED.close_reason,
                  payoff_curve = EXCLUDED.payoff_curve,
                  metadata = EXCLUDED.metadata,
                  closed_at = EXCLUDED.closed_at,
                  updated_at = NOW()
                """,
                {
                    "id": st.strategy_id,
                    "aid": account_id,
                    "slug": st.account_slug,
                    "stype": st.strategy_type.value,
                    "label": label_he,
                    "sym": st.underlying_symbol,
                    "status": st.status.value,
                    "mode": st.mode.value,
                    "qty": int(st.contracts_qty),
                    "exp": st.expiration_date,
                    "is_credit": st.is_credit,
                    "net_cash": money(st.net_entry_cash_flow),
                    "liab": st.short_liability() if st.is_open else ZERO,
                    "lval": st.long_value() if st.is_open else ZERO,
                    "coll": money(st.collateral_locked) if st.is_open else ZERO,
                    "maxp": money(st.max_profit_usd) if st.max_profit_usd is not None else None,
                    "maxl": money(st.max_loss_usd),
                    "unreal": unrealized,
                    "real": money(st.realized_pnl),
                    "fees": money(st.fees_paid),
                    "nd": round(net_delta, 4),
                    "nt": round(net_theta, 4),
                    "nv": round(net_vega, 4),
                    "pin": st.pin_risk_warning,
                    "early": st.early_assignment_warning,
                    "wheel": st.wheel_state,
                    "rationale": st.ai_rationale,
                    "reason": st.close_reason,
                    "curve": _jdumps(payoff_pts),
                    "meta": _jdumps(meta_payload),
                    "opened": st.opened_at,
                    "closed": st.closed_at,
                },
            )

            order_id: Optional[str] = None
            if is_new:
                order_id = _as_uuid(f"ord:{st.strategy_id}:open")
                self._exec(
                    """
                    INSERT INTO opt_orders (
                      id, account_id, strategy_id, action_kind, legs_payload, order_status,
                      idempotency_key, created_at, filled_at
                    ) VALUES (
                      CAST(:oid AS uuid), CAST(:aid AS uuid), CAST(:sid AS uuid), 'OPEN',
                      CAST(:payload AS jsonb), 'filled', :ikey, :ts, :ts
                    ) ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    {
                        "oid": order_id,
                        "aid": account_id,
                        "sid": st.strategy_id,
                        "payload": _jdumps([{"occ": l.occ_symbol, "side": l.side.value, "qty": l.quantity} for l in st.legs]),
                        "ikey": f"sim_open:{st.strategy_id}",
                        "ts": st.opened_at,
                    },
                )

            for leg in st.legs:
                contract_id = None
                if leg.is_option and leg.occ_symbol and leg.right and leg.strike is not None and leg.expiration_date:
                    contract_id = self.repo.upsert_contract(
                        OptionContractSpec(
                            occ_symbol=leg.occ_symbol,
                            underlying_symbol=leg.underlying_symbol,
                            right=leg.right,
                            strike=leg.strike,
                            expiration_date=leg.expiration_date,
                            multiplier=leg.multiplier,
                            exercise_style=leg.exercise_style,
                        )
                    )
                self._exec(
                    """
                    INSERT INTO opt_legs (
                      id, strategy_id, contract_id, occ_symbol, underlying_symbol, leg_role,
                      side, option_right, strike, expiration_date, multiplier, quantity,
                      entry_price, current_mark, bid, ask, unrealized_pnl, realized_pnl,
                      delta, gamma, theta, vega, implied_volatility, status, updated_at
                    ) VALUES (
                      CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:cid AS uuid), :occ, :sym, :role,
                      :side, :right, :strike, :exp, :mult, :qty,
                      :entry, :mark, :bid, :ask, :unreal, :real,
                      :delta, :gamma, :theta, :vega, :iv, :status, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      current_mark = EXCLUDED.current_mark,
                      bid = EXCLUDED.bid,
                      ask = EXCLUDED.ask,
                      unrealized_pnl = EXCLUDED.unrealized_pnl,
                      realized_pnl = EXCLUDED.realized_pnl,
                      delta = EXCLUDED.delta,
                      gamma = EXCLUDED.gamma,
                      theta = EXCLUDED.theta,
                      vega = EXCLUDED.vega,
                      implied_volatility = EXCLUDED.implied_volatility,
                      status = EXCLUDED.status,
                      updated_at = NOW()
                    """,
                    {
                        "id": leg.leg_id,
                        "sid": st.strategy_id,
                        "cid": contract_id,
                        "occ": leg.occ_symbol,
                        "sym": leg.underlying_symbol,
                        "role": leg.leg_role.value,
                        "side": leg.side.value,
                        "right": leg.right.value if leg.right else None,
                        "strike": leg.strike,
                        "exp": leg.expiration_date,
                        "mult": int(leg.multiplier),
                        "qty": int(leg.quantity),
                        "entry": leg.entry_price,
                        "mark": leg.current_mark,
                        "bid": leg.bid,
                        "ask": leg.ask,
                        "unreal": leg.unrealized_pnl() if leg.is_open else ZERO,
                        "real": money(leg.realized_pnl),
                        "delta": leg.greeks.delta,
                        "gamma": leg.greeks.gamma,
                        "theta": leg.greeks.theta,
                        "vega": leg.greeks.vega,
                        "iv": leg.implied_volatility,
                        "status": leg.status,
                    },
                )
                if is_new and order_id is not None:
                    self._exec(
                        """
                        INSERT INTO opt_fills (
                          order_id, strategy_id, leg_id, occ_symbol, side, quantity, multiplier,
                          fill_price, bid_at_fill, ask_at_fill, slippage_usd, fee_usd, cash_flow_usd,
                          feed_tier, is_indicative, filled_at
                        ) VALUES (
                          CAST(:oid AS uuid), CAST(:sid AS uuid), CAST(:lid AS uuid), :occ, :side, :qty, :mult,
                          :price, :bid, :ask, 0, 0, :cash, :tier, :ind, :ts
                        )
                        """,
                        {
                            "oid": order_id,
                            "sid": st.strategy_id,
                            "lid": leg.leg_id,
                            "occ": leg.occ_symbol,
                            "side": leg.side.value,
                            "qty": int(leg.quantity),
                            "mult": int(leg.multiplier),
                            "price": leg.entry_price,
                            "bid": leg.bid,
                            "ask": leg.ask,
                            "cash": money(leg.entry_value() * (-1 if leg.side == OptionSide.LONG else 1)),
                            "tier": str(st.metadata.get("feed_tier") or "alpaca_indicative"),
                            "ind": bool(st.metadata.get("is_indicative", True)),
                            "ts": st.opened_at,
                        },
                    )

        # 4) Append ledger events idempotently
        for ev in events:
            ev_id = _as_uuid(ev.event_id)
            ev.event_id = ev_id
            sid = strat_id_map.get(ev.strategy_group_id, _as_uuid(ev.strategy_group_id)) if ev.strategy_group_id else None
            self._exec(
                """
                INSERT INTO opt_ledger_events (
                  id, account_id, account_slug, strategy_id, event_type, underlying_symbol,
                  strategy_type, cash_delta, realized_pnl_delta, collateral_delta, fee_usd,
                  description, metadata, created_at
                ) VALUES (
                  CAST(:id AS uuid), CAST(:aid AS uuid), :slug, CAST(:sid AS uuid), :etype, :sym,
                  :stype, :cd, :rd, :colld, :fee, :desc, CAST(:meta AS jsonb), :ts
                ) ON CONFLICT (id) DO NOTHING
                """,
                {
                    "id": ev_id,
                    "aid": account_id,
                    "slug": ev.account_slug,
                    "sid": sid,
                    "etype": ev.event_type,
                    "sym": ev.underlying_symbol,
                    "stype": ev.strategy_type,
                    "cd": money(ev.cash_delta),
                    "rd": money(ev.realized_pnl_delta),
                    "colld": money(ev.collateral_delta),
                    "fee": money(ev.fee_usd),
                    "desc": ev.description,
                    "meta": _jdumps(ev.metadata),
                    "ts": ev.created_at,
                },
            )

        # 5) Recompute authoritative account totals & persist
        all_open = self.list_open_strategies(account.slug)
        totals = compute_account_totals(account, all_open)
        summary = to_account_summary(account, all_open)
        rmeta_out = {
            "day_start_date": account.day_start_date.isoformat() if account.day_start_date else None,
            "day_start_equity": str(account.day_start_equity) if account.day_start_equity is not None else None,
            "day_trade_dates": [d.isoformat() for d in account.day_trade_dates[-30:]],
            "last_spot_by_symbol": {k: str(v) for k, v in account.last_spot_by_symbol.items()},
            "lot_meta": lot_meta,
        }
        self._exec(
            """
            UPDATE opt_accounts SET
              cash_balance = :cash,
              cash_reserved = :res,
              margin_used = :marg,
              long_options_market_value = :lval,
              short_options_liability = :liab,
              stock_holdings_value = :sval,
              equity = :eq,
              peak_equity = :peak,
              max_drawdown_usd = :mdd_u,
              max_drawdown_pct = :mdd_p,
              premium_collected_gross = :pcg,
              premium_realized_net = :prn,
              unrealized_pnl = :unreal,
              realized_pnl = :real,
              fees_paid = :fees,
              wheel_states = CAST(:wheel AS jsonb),
              runtime_meta = CAST(:rmeta AS jsonb),
              updated_at = NOW()
            WHERE slug = :slug
            """,
            {
                "slug": account.slug,
                "cash": money(account.cash_balance),
                "res": money(account.cash_reserved),
                "marg": Decimal(str(round(summary.margin_used, 2))),
                "lval": money(totals.long_options_market_value),
                "liab": money(totals.short_options_liability),
                "sval": money(totals.stock_holdings_value),
                "eq": money(totals.equity),
                "peak": money(account.peak_equity),
                "mdd_u": money(account.max_drawdown_usd),
                "mdd_p": account.max_drawdown_pct,
                "pcg": money(account.premium_collected_gross),
                "prn": money(account.premium_realized_net),
                "unreal": money(totals.unrealized_pnl),
                "real": money(account.realized_pnl),
                "fees": money(account.fees_paid),
                "wheel": _jdumps(account.wheel_states),
                "rmeta": _jdumps(rmeta_out),
            },
        )
        if self.repo.autocommit:
            self.session.commit()
