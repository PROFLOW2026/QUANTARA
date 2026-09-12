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
from quantara_engine.live_sim.candidate_log import log_allocation, update_allocation_execution
from quantara_engine.live_sim.constants import REJECTION_HE
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
from quantara_engine.market_data.assets import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore
from quantara_engine.pipeline.candle_processor import signal_age_minutes
from quantara_engine.risk.opportunity import opportunity_key_from_signal
from quantara_engine.risk.sizing import select_quantity_for_risk_budget

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


def _already_seen(store: TradingStore, account_id: str, canonical_key: str) -> bool:
    row = store.session.execute(
        text(
            """
            SELECT 1 FROM live_sim_allocation_log
            WHERE broker_account_id = :aid AND canonical_opportunity_key = :key
            LIMIT 1
            """
        ),
        {"aid": account_id, "key": canonical_key},
    ).first()
    return row is not None


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

    if _already_seen(store, account_id, canonical_key):
        return {"status": "skipped", "reason": "duplicate"}

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

    from quantara_engine.risk.sizing import _desired_quantity_for_risk

    desired_qty = _desired_quantity_for_risk(
        target_risk, sl_distance, instrument, ctx.fx_rates
    )
    qty, expected_risk, deny = select_quantity_for_risk_budget(
        target_risk=target_risk,
        desired_quantity=desired_qty,
        instrument=instrument,
        direction=dir_enum,
        entry_reference=entry_ref,
        stop_loss=sl,
        fx_rates=ctx.fx_rates,
        execution_assumptions=assumptions,
    )

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
            metadata={"pending_execution": True},
        )
        return {"status": "queued", "log_id": log_id}

    exec_candle = candles[candle_index + 1]
    broker_adapter = PaperBrokerAdapter(instrument.id, assumptions)
    intent = OrderIntent(
        id=new_id(),
        signal_id="",
        strategy_instance_id=instance.id,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        direction=dir_enum,
        quantity=qty,
        stop_loss=sl,
        take_profit=signal.suggested_tp,
        target_risk_amount=target_risk,
        actual_risk_amount=expected_risk,
        signal_candle_timestamp=candle.timestamp,
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
        detail = (
            broker_res.decision.rejection_detail
            if broker_res and broker_res.decision
            else "broker rejected"
        )
        return _reject("BROKER_REJECTED", detail or REJECTION_HE["BROKER_REJECTED"])

    pos_id = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO live_sim_positions (
              id, broker_account_id, instrument_id, strategy_slug, strategy_version,
              robot_label, timeframe, direction, quantity, entry_price, stop_loss,
              take_profit, current_price, planned_sl_risk_usd, opportunity_key,
              canonical_opportunity_key, status, opened_at
            ) VALUES (
              :id, :aid, :iid, :slug, :ver, :robot, :tf, CAST(:dir AS direction),
              :qty, :entry, :sl, :tp, :mark, :risk, :opp, :canonical, 'open', :opened
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
            "tp": signal.suggested_tp,
            "mark": fill.fill_price,
            "risk": expected_risk,
            "opp": opportunity_key,
            "canonical": canonical_key,
            "opened": exec_candle.timestamp,
        },
    )

    sym_pct = float((open_risk.by_symbol.get(sym, Decimal("0")) + expected_risk) / equity * 100)
    grp_pct = (
        float((open_risk.by_group.get(grp, Decimal("0")) + expected_risk) / equity * 100)
        if grp
        else None
    )
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
        symbol_sl_risk_pct=sym_pct,
        group_sl_risk_pct=grp_pct,
        group_name=grp,
        broker_order_id=broker_res.broker_order_id,
        live_sim_position_id=pos_id,
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
