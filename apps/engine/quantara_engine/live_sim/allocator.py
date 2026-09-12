"""Live-sim candidate evaluation — dedupe, size, gate, queue execution."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.competition.robot_registry import ROBOT_LABELS
from quantara_engine.domain.types import Direction, IntentStatus, OrderIntent, SignalAction, new_id
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.timing import freshness_max_age_minutes
from quantara_engine.live_sim.candidate_log import (
    allocation_lifecycle_state,
    expire_stale_live_sim_allocations,
    find_allocation_by_canonical,
    log_allocation,
    mark_allocation_expired,
    update_allocation_execution,
    update_allocation_metadata,
)
from quantara_engine.live_sim.constants import REJECTION_HE
from quantara_engine.live_sim.sizing import size_live_sim_entry
from quantara_engine.live_sim.opportunity import (
    live_sim_canonical_opportunity_key,
    live_sim_execution_idempotency_key,
)
from quantara_engine.live_sim.risk_policy import (
    compute_open_sl_risk,
    evaluate_entry_gates,
    load_risk_settings,
    maybe_roll_daily_start,
    symbol_risk_group,
    target_risk_for_equity,
    update_high_water_mark,
)
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore
from quantara_engine.pipeline.candle_processor import signal_age_minutes
from quantara_engine.risk.opportunity import opportunity_key_from_signal
from quantara_engine.execution.timing import live_fill_allowed

logger = logging.getLogger(__name__)


def _account_row(store: TradingStore) -> dict | None:
    return store.session.execute(
        text(
            """
            SELECT id::text, slug, equity, balance, cash, unrealized_pnl, realized_pnl,
                   starting_cash, is_active, pending_owner_reset, account_state::text,
                   activated_at, risk_settings
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()


def _execution_candle_index(candles: list, signal_candle_timestamp: datetime) -> int | None:
    for idx, row in enumerate(candles):
        if row.timestamp == signal_candle_timestamp:
            return idx
    return None


def _execute_accepted_allocation(
    store: TradingStore,
    *,
    account_id: str,
    account: dict,
    log_id: str,
    canonical_key: str,
    opportunity_key: str,
    strategy_slug: str,
    strategy_version: str,
    robot_label: str,
    instance,
    instrument,
    direction: str,
    dir_enum: Direction,
    signal_candle_timestamp: datetime,
    entry_ref: Decimal,
    sl: Decimal,
    take_profit,
    qty: Decimal,
    expected_risk: Decimal,
    target_risk: Decimal,
    exec_candle,
    open_risk,
    equity: Decimal,
    sizing_reason: str | None = None,
) -> dict:
    sym = instrument.symbol.upper().replace("/", "")
    grp = symbol_risk_group(sym)
    assumptions = execution_assumptions_for(instrument, exec_candle.close)
    broker_adapter = PaperBrokerAdapter(instrument.id, assumptions)
    intent = OrderIntent(
        id=new_id(),
        signal_id="",
        strategy_instance_id=instance.id,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        direction=dir_enum,
        quantity=qty,
        stop_loss=sl,
        take_profit=take_profit,
        target_risk_amount=target_risk,
        actual_risk_amount=expected_risk,
        signal_candle_timestamp=signal_candle_timestamp,
        risk_profile_id="",
        status=IntentStatus.PENDING_EXECUTION,
    )
    _, fill = broker_adapter.execute_entry(intent, exec_candle)
    idem = live_sim_execution_idempotency_key(LIVE_SIM_10K_ACCOUNT_SLUG, canonical_key)
    broker_res = execute_through_broker(
        store,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        instrument=instrument,
        direction=dir_enum,
        quantity=qty,
        fill=fill,
        execution_at=exec_candle.timestamp,
        timeframe=instance.timeframe,
        idempotency_key=idem,
        opportunity_key=opportunity_key,
        order_purpose="entry",
        skip_if_not_competition=False,
        account_slug=LIVE_SIM_10K_ACCOUNT_SLUG,
    )

    if broker_res is None or not broker_res.accepted:
        from quantara_engine.broker.display import broker_reason_he

        reason_code = (
            broker_res.decision.rejection_reason.value
            if broker_res and broker_res.decision and broker_res.decision.rejection_reason
            else "broker_rejected"
        )
        return {
            "status": "rejected",
            "reason": "BROKER_REJECTED",
            "detail": broker_reason_he(reason_code),
        }

    pos_id = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO live_sim_positions (
              id, broker_account_id, instrument_id, strategy_slug, strategy_version,
              robot_label, timeframe, direction, quantity, entry_price, stop_loss,
              take_profit, current_price, planned_sl_risk_usd, opportunity_key,
              canonical_opportunity_key, status, opened_at, allocation_log_id
            ) VALUES (
              :id, :aid, :iid, :slug, :ver, :robot, :tf, CAST(:dir AS direction),
              :qty, :entry, :sl, :tp, :mark, :risk, :opp, :canonical, 'open', :opened, :log_id
            )
            """
        ),
        {
            "id": pos_id,
            "aid": account_id,
            "iid": instrument.id,
            "slug": strategy_slug,
            "ver": strategy_version,
            "robot": robot_label,
            "tf": instance.timeframe,
            "dir": direction,
            "qty": qty,
            "entry": fill.fill_price,
            "sl": sl,
            "tp": take_profit,
            "mark": fill.fill_price,
            "risk": expected_risk,
            "opp": opportunity_key,
            "canonical": canonical_key,
            "opened": exec_candle.timestamp,
            "log_id": log_id,
        },
    )

    sym_pct = float((open_risk.by_symbol.get(sym, Decimal("0")) + expected_risk) / equity * 100)
    grp_pct = (
        float((open_risk.by_group.get(grp, Decimal("0")) + expected_risk) / equity * 100)
        if grp
        else None
    )
    update_allocation_execution(
        store,
        log_id,
        broker_order_id=broker_res.broker_order_id,
        live_sim_position_id=pos_id,
        metadata_patch={
            "pending_execution": False,
            "lifecycle_state": "filled",
            "sizing_reason": sizing_reason,
        },
    )
    store.session.execute(
        text(
            """
            UPDATE live_sim_allocation_log
            SET calculated_risk_usd = :risk,
                calculated_quantity = :qty,
                resulting_open_sl_risk_usd = :open_risk,
                symbol_sl_risk_pct = :sym_pct,
                group_sl_risk_pct = :grp_pct,
                group_name = :grp
            WHERE id = :id
            """
        ),
        {
            "id": log_id,
            "risk": expected_risk,
            "qty": qty,
            "open_risk": open_risk.total_sl_risk_usd + expected_risk,
            "sym_pct": sym_pct,
            "grp_pct": grp_pct,
            "grp": grp,
        },
    )

    svc = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
    svc.mark_to_market({instrument.symbol.upper(): exec_candle.close}, at=exec_candle.timestamp)

    logger.info(
        "Live-sim entry %s %s qty=%s risk=$%s",
        instrument.symbol,
        direction,
        qty,
        expected_risk,
    )
    return {"status": "accepted", "log_id": log_id, "position_id": pos_id}


def _resume_pending_allocation(
    store: TradingStore,
    *,
    existing: dict,
    account_id: str,
    account: dict,
    entry: dict,
    instrument,
    candles: list,
    execution_now: datetime,
) -> dict:
    lifecycle = allocation_lifecycle_state(existing)
    if lifecycle != "pending_execution":
        return {"status": "skipped", "reason": lifecycle}

    signal_ts = existing["signal_candle_timestamp"]
    candle_index = _execution_candle_index(candles, signal_ts)
    if candle_index is None:
        return {"status": "queued", "log_id": existing["id"]}

    exec_index = candle_index + 1
    if exec_index >= len(candles):
        return {"status": "queued", "log_id": existing["id"]}

    exec_candle = candles[exec_index]
    allowed, reject_reason = live_fill_allowed(
        execution_candle_timestamp=exec_candle.timestamp,
        candle_timestamp=exec_candle.timestamp,
        signal_candle_timestamp=signal_ts,
        now=execution_now,
        timeframe=str(existing["timeframe"]),
    )
    if not allowed:
        if reject_reason and "execution_window_passed" in reject_reason:
            mark_allocation_expired(store, existing["id"], reason=reject_reason)
            return {"status": "expired", "log_id": existing["id"]}
        return {"status": "queued", "log_id": existing["id"]}

    from quantara_engine.broker.capability import check_entry_capability_for_account
    from quantara_engine.broker.display import broker_reason_he
    from quantara_engine.live_sim.candidate_log import mark_allocation_rejected

    cap = check_entry_capability_for_account(
        LIVE_SIM_10K_ACCOUNT_SLUG,
        instrument,
        str(existing["direction"]),
    )
    if not cap.allowed:
        detail = broker_reason_he(cap.reason)
        mark_allocation_rejected(
            store,
            existing["id"],
            rejection_reason="BROKER_CAPABILITY_DENIED",
            rejection_detail=detail,
        )
        return {"status": "rejected", "reason": "BROKER_CAPABILITY_DENIED", "detail": detail}

    instance = entry["instance"]
    equity = Decimal(str(account["equity"] or account["starting_cash"]))
    open_risk = compute_open_sl_risk(store, account_id)
    dir_enum = Direction.LONG if existing["direction"] == "long" else Direction.SHORT
    qty = Decimal(str(existing["calculated_quantity"]))
    expected_risk = Decimal(str(existing["calculated_risk_usd"] or 0))
    target_risk = target_risk_for_equity(
        equity, load_risk_settings(dict(account))
    )

    return _execute_accepted_allocation(
        store,
        account_id=account_id,
        account=account,
        log_id=existing["id"],
        canonical_key=str(existing.get("canonical_opportunity_key") or ""),
        opportunity_key=existing.get("opportunity_key") or "",
        strategy_slug=existing["strategy_slug"],
        strategy_version=existing.get("strategy_version") or "1.0.0",
        robot_label=existing.get("robot_label") or existing["strategy_slug"],
        instance=instance,
        instrument=instrument,
        direction=existing["direction"],
        dir_enum=dir_enum,
        signal_candle_timestamp=signal_ts,
        entry_ref=Decimal(str(existing["proposed_entry"])),
        sl=Decimal(str(existing["stop_loss"])),
        take_profit=Decimal(str(existing["take_profit"])) if existing.get("take_profit") else None,
        qty=qty,
        expected_risk=expected_risk,
        target_risk=target_risk,
        exec_candle=exec_candle,
        open_risk=open_risk,
        equity=equity,
        sizing_reason=(existing.get("metadata") or {}).get("sizing_reason"),
    )


def resume_all_pending_live_sim_allocations(
    store: TradingStore,
    execution_now: datetime,
) -> dict:
    """Resume queued live-sim allocations independent of current signal evaluation."""
    account = _account_row(store)
    if not account or not account.get("is_active"):
        return {"resumed": 0, "expired": 0}

    expired = expire_stale_live_sim_allocations(store, execution_now)
    rows = store.session.execute(
        text(
            """
            SELECT id::text, canonical_opportunity_key, strategy_slug, symbol, timeframe
            FROM live_sim_allocation_log
            WHERE broker_account_id = :aid
              AND accepted = TRUE
              AND broker_order_id IS NULL
              AND live_sim_position_id IS NULL
              AND (metadata->>'pending_execution')::boolean IS TRUE
              AND COALESCE((metadata->>'expired')::boolean, FALSE) = FALSE
            """
        ),
        {"aid": account["id"]},
    ).mappings().all()

    resumed = 0
    for row in rows:
        full = find_allocation_by_canonical(
            store, account["id"], row["canonical_opportunity_key"]
        )
        if not full:
            continue
        instrument = store.get_instrument_by_symbol(str(full["symbol"]))
        if not instrument:
            continue
        candles = store.list_recent_candles(
            instrument.id, str(full["timeframe"]), limit=120
        )
        if not candles:
            continue
        entry = {
            "instance": type(
                "Inst",
                (),
                {
                    "id": LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                    "strategy_slug": full["strategy_slug"],
                    "timeframe": full["timeframe"],
                },
            )()
        }
        result = _resume_pending_allocation(
            store,
            existing=full,
            account_id=account["id"],
            account=account,
            entry=entry,
            instrument=instrument,
            candles=candles,
            execution_now=execution_now,
        )
        if result.get("status") == "accepted":
            resumed += 1
    return {"resumed": resumed, "expired": expired}


def maybe_allocate_live_sim(
    store: TradingStore,
    *,
    entry: dict,
    instrument,
    candle,
    candles: list,
    candle_index: int,
    signal,
    execution_now: datetime,
) -> dict:
    """Evaluate one canonical candidate for live-sim account (once per opportunity)."""
    result = {"status": "skipped"}
    if signal is None or signal.action not in (SignalAction.BUY, SignalAction.SELL):
        return result

    account = _account_row(store)
    if not account or not account.get("is_active") or account.get("pending_owner_reset"):
        return {"status": "skipped", "reason": "account_inactive"}

    account_id = account["id"]
    instance = entry["instance"]
    strategy_slug = instance.strategy_slug
    strategy_version = getattr(instance, "strategy_version", None) or "1.0.0"
    robot_label = ROBOT_LABELS.get(strategy_slug, strategy_slug)
    direction = "long" if signal.action == SignalAction.BUY else "short"
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT

    opportunity_key = opportunity_key_from_signal(
        signal,
        symbol=instrument.symbol,
        timeframe=instance.timeframe,
        strategy_slug=strategy_slug,
        setup_candle_timestamp=candle.timestamp,
        strategy_version=strategy_version,
    )
    if not opportunity_key:
        return {"status": "skipped", "reason": "no_opportunity_key"}

    canonical_key = live_sim_canonical_opportunity_key(
        strategy_slug=strategy_slug,
        strategy_version=strategy_version,
        symbol=instrument.symbol,
        timeframe=instance.timeframe,
        direction=direction,
        opportunity_key=opportunity_key,
    )

    existing = find_allocation_by_canonical(store, account_id, canonical_key)
    if existing:
        lifecycle = allocation_lifecycle_state(existing)
        if lifecycle == "pending_execution":
            return _resume_pending_allocation(
                store,
                existing=existing,
                account_id=account_id,
                account=account,
                entry=entry,
                instrument=instrument,
                candles=candles,
                execution_now=execution_now,
            )
        return {"status": "skipped", "reason": lifecycle}

    if signal_age_minutes(candle.timestamp, execution_now) > freshness_max_age_minutes(
        instance.timeframe
    ):
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=candle.close,
            stop_loss=signal.suggested_sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=None,
            calculated_quantity=None,
            accepted=False,
            rejection_reason="STALE_SIGNAL",
            rejection_detail=REJECTION_HE["STALE_SIGNAL"],
        )
        return {"status": "rejected", "reason": "STALE_SIGNAL"}

    asset = get_asset(instrument.symbol)
    if asset and not session_allows_entries(asset.trading_sessions, candle.timestamp):
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=candle.close,
            stop_loss=signal.suggested_sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=None,
            calculated_quantity=None,
            accepted=False,
            rejection_reason="SESSION_CLOSED",
            rejection_detail=REJECTION_HE["SESSION_CLOSED"],
        )
        return {"status": "rejected", "reason": "SESSION_CLOSED"}

    from quantara_engine.broker.capability import check_entry_capability_for_account
    from quantara_engine.broker.display import broker_reason_he

    cap = check_entry_capability_for_account(
        LIVE_SIM_10K_ACCOUNT_SLUG,
        instrument,
        direction,
    )
    if not cap.allowed:
        detail = broker_reason_he(cap.reason)
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=candle.close,
            stop_loss=signal.suggested_sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=None,
            calculated_quantity=None,
            accepted=False,
            rejection_reason="BROKER_CAPABILITY_DENIED",
            rejection_detail=detail,
        )
        return {"status": "rejected", "reason": "BROKER_CAPABILITY_DENIED", "detail": detail}

    if signal.suggested_sl is None:
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=candle.close,
            stop_loss=None,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=None,
            calculated_quantity=None,
            accepted=False,
            rejection_reason="INVALID_STOP_LOSS",
            rejection_detail=REJECTION_HE["INVALID_STOP_LOSS"],
        )
        return {"status": "rejected", "reason": "INVALID_STOP_LOSS"}

    equity = Decimal(str(account["equity"] or account["starting_cash"]))
    settings = load_risk_settings(dict(account))
    settings = maybe_roll_daily_start(store, account_id, settings, equity, execution_now)
    update_high_water_mark(store, account_id, equity)

    ctx = store.build_currency_context_for_instruments([instrument])
    assumptions = execution_assumptions_for(instrument, candle.close)
    entry_ref = candle.close
    sl = Decimal(str(signal.suggested_sl))

    target_risk = target_risk_for_equity(equity, settings)
    sl_distance = abs(entry_ref - sl)
    if sl_distance <= 0:
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=entry_ref,
            stop_loss=sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=None,
            calculated_quantity=None,
            accepted=False,
            rejection_reason="INVALID_STOP_LOSS",
            rejection_detail=REJECTION_HE["INVALID_STOP_LOSS"],
        )
        return {"status": "rejected", "reason": "INVALID_STOP_LOSS"}

    cash = Decimal(str(account.get("cash") or account.get("equity") or account["starting_cash"]))
    sizing = size_live_sim_entry(
        equity=equity,
        cash=cash,
        target_risk=target_risk,
        entry_reference=entry_ref,
        stop_loss=sl,
        direction=dir_enum,
        instrument=instrument,
        fx_rates=ctx.fx_rates,
        execution_assumptions=assumptions,
    )
    qty = sizing.quantity
    expected_risk = sizing.expected_risk_usd
    deny = sizing.deny_reason

    open_risk = compute_open_sl_risk(store, account_id)
    sym = instrument.symbol.upper().replace("/", "")
    grp = symbol_risk_group(sym)

    def _reject(reason: str, detail: str) -> dict:
        sym_pct = (
            float((open_risk.by_symbol.get(sym, Decimal("0")) + (expected_risk or Decimal("0"))))
            / equity
            * 100
        ) if equity > 0 else 0.0
        grp_pct = (
            float((open_risk.by_group.get(grp, Decimal("0")) + (expected_risk or Decimal("0"))))
            / equity
            * 100
        ) if equity > 0 and grp else None
        log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=entry_ref,
            stop_loss=sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=expected_risk,
            calculated_quantity=qty,
            accepted=False,
            rejection_reason=reason,
            rejection_detail=detail,
            resulting_open_sl_risk_usd=open_risk.total_sl_risk_usd,
            symbol_sl_risk_pct=sym_pct,
            group_sl_risk_pct=grp_pct,
            group_name=grp,
        )
        return {"status": "rejected", "reason": reason}

    if deny or qty <= 0:
        reason = "MIN_QUANTITY_EXCEEDS_RISK_BUDGET" if deny else "MIN_QUANTITY"
        return _reject(reason, REJECTION_HE.get(reason, deny or reason))

    gate = evaluate_entry_gates(
        settings=settings,
        equity=equity,
        realized_pnl_today=Decimal("0"),
        open_risk=open_risk,
        proposed_risk_usd=expected_risk,
        symbol=instrument.symbol,
    )
    if not gate.allowed:
        return _reject(gate.reason or "GATE", gate.detail or REJECTION_HE.get(gate.reason or "", ""))

    pending_meta = {
        "pending_execution": True,
        "lifecycle_state": "pending_execution",
        "sizing_reason": sizing.sizing_reason,
    }
    if candle_index + 1 >= len(candles):
        log_id = log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=entry_ref,
            stop_loss=sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=expected_risk,
            calculated_quantity=qty,
            accepted=True,
            resulting_open_sl_risk_usd=open_risk.total_sl_risk_usd + expected_risk,
            metadata=pending_meta,
        )
        return {"status": "queued", "log_id": log_id}

    exec_candle = candles[candle_index + 1]
    allowed, reject_reason = live_fill_allowed(
        execution_candle_timestamp=exec_candle.timestamp,
        candle_timestamp=exec_candle.timestamp,
        signal_candle_timestamp=candle.timestamp,
        now=execution_now,
        timeframe=instance.timeframe,
    )
    if not allowed:
        log_id = log_allocation(
            store,
            account_id=account_id,
            canonical_key=canonical_key,
            opportunity_key=opportunity_key,
            strategy_slug=strategy_slug,
            strategy_version=strategy_version,
            robot_label=robot_label,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            signal_candle_timestamp=candle.timestamp,
            proposed_entry=entry_ref,
            stop_loss=sl,
            take_profit=signal.suggested_tp,
            calculated_risk_usd=expected_risk,
            calculated_quantity=qty,
            accepted=True,
            resulting_open_sl_risk_usd=open_risk.total_sl_risk_usd + expected_risk,
            metadata=pending_meta,
        )
        return {"status": "queued", "log_id": log_id, "reason": reject_reason}

    log_id = log_allocation(
        store,
        account_id=account_id,
        canonical_key=canonical_key,
        opportunity_key=opportunity_key,
        strategy_slug=strategy_slug,
        strategy_version=strategy_version,
        robot_label=robot_label,
        symbol=instrument.symbol,
        timeframe=instance.timeframe,
        direction=direction,
        signal_candle_timestamp=candle.timestamp,
        proposed_entry=entry_ref,
        stop_loss=sl,
        take_profit=signal.suggested_tp,
        calculated_risk_usd=expected_risk,
        calculated_quantity=qty,
        accepted=True,
        resulting_open_sl_risk_usd=open_risk.total_sl_risk_usd + expected_risk,
        symbol_sl_risk_pct=float(
            (open_risk.by_symbol.get(sym, Decimal("0")) + expected_risk) / equity * 100
        ),
        group_sl_risk_pct=(
            float((open_risk.by_group.get(grp, Decimal("0")) + expected_risk) / equity * 100)
            if grp
            else None
        ),
        group_name=grp,
        metadata={"lifecycle_state": "executing", "sizing_reason": sizing.sizing_reason},
    )
    if not log_id:
        return {"status": "skipped", "reason": "duplicate"}

    exec_result = _execute_accepted_allocation(
        store,
        account_id=account_id,
        account=account,
        log_id=log_id,
        canonical_key=canonical_key,
        opportunity_key=opportunity_key,
        strategy_slug=strategy_slug,
        strategy_version=strategy_version,
        robot_label=robot_label,
        instance=instance,
        instrument=instrument,
        direction=direction,
        dir_enum=dir_enum,
        signal_candle_timestamp=candle.timestamp,
        entry_ref=entry_ref,
        sl=sl,
        take_profit=signal.suggested_tp,
        qty=qty,
        expected_risk=expected_risk,
        target_risk=target_risk,
        exec_candle=exec_candle,
        open_risk=open_risk,
        equity=equity,
        sizing_reason=sizing.sizing_reason,
    )
    if exec_result.get("status") == "rejected":
        reason = exec_result.get("reason", "BROKER_REJECTED")
        detail = exec_result.get("detail") or REJECTION_HE.get(reason, REJECTION_HE["BROKER_REJECTED"])
        return _reject(reason, detail)
    return exec_result
