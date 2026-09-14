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
    mark_allocation_rejected,
    update_allocation_execution,
    update_allocation_metadata,
)
from quantara_engine.live_sim.constants import REJECTION_HE
from quantara_engine.live_sim.account_bootstrap import repair_pristine_live_sim_spot_crypto_cash
from quantara_engine.live_sim.sizing import (
    entry_mark_price_for_sizing,
    size_live_sim_entry,
    validate_live_sim_broker_pre_trade,
)
from quantara_engine.broker.spot_crypto_cash import effective_spot_crypto_cash
from quantara_engine.live_sim.execution_routing import (
    broker_account_row_by_id,
    resolve_live_sim_runtime_context,
)
from quantara_engine.live_sim.opportunity import (
    live_sim_canonical_opportunity_key,
    live_sim_execution_idempotency_key,
)
from quantara_engine.live_sim.asset_gate_settings import (
    load_asset_gate_settings,
    maybe_roll_asset_daily_start,
    update_asset_high_water_mark,
)
from quantara_engine.live_sim.risk_policy import (
    compute_open_sl_risk,
    evaluate_drawdown_gate,
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
from quantara_engine.execution.timing import (
    is_execution_candle_ready,
    next_execution_timestamp,
    resolve_execution_candle,
)

logger = logging.getLogger(__name__)


def _equal_asset_sizing_context(
    store: TradingStore,
    *,
    symbol: str,
    account: dict,
) -> tuple[Decimal, Decimal, str, dict] | None:
    """When equal-asset multi-broker is active, size from isolated asset envelope."""
    from quantara_engine.owner_portfolio.asset_allocation import (
        asset_equity,
        get_asset_allocation_row,
        is_equal_asset_mode_active,
    )
    from quantara_engine.owner_portfolio.asset_ledger import asset_available_cash
    from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

    if not is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        return None
    row = get_asset_allocation_row(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol=symbol)
    if not row or not row.get("enabled"):
        return None
    equity = asset_equity(row)
    cash = asset_available_cash(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol=symbol)
    broker_id = row.get("broker_account_id") or account["id"]
    return equity, cash, str(broker_id), row


def _account_row(store: TradingStore, slug: str = LIVE_SIM_10K_ACCOUNT_SLUG) -> dict | None:
    from quantara_engine.live_sim.execution_routing import broker_account_row

    row = broker_account_row(store, slug)
    return dict(row) if row else None


def _current_asset_notional_usd(store: TradingStore, account_id: str, db_symbol: str) -> Decimal:
    from quantara_engine.broker.instruments import get_instrument_spec
    from quantara_engine.broker.margin import quote_notional_usd

    sym = db_symbol.upper().replace("/", "")
    row = store.session.execute(
        text(
            """
            SELECT bp.net_quantity, bp.mark_price
            FROM broker_positions bp
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE bp.broker_account_id = :aid AND i.symbol = :sym
            """
        ),
        {"aid": account_id, "sym": sym},
    ).mappings().first()
    if not row or not row["net_quantity"]:
        return Decimal("0")
    qty = abs(Decimal(str(row["net_quantity"])))
    mark = Decimal(str(row["mark_price"]))
    spec = get_instrument_spec(sym)
    instrument = store.get_instrument_by_symbol(sym)
    if instrument is None:
        # Fallback: USD-quoted specs only; non-USD requires FX context.
        if spec.quote_currency.upper() == "USD":
            return quote_notional_usd(qty, mark, spec, {})
        return Decimal("0")
    ctx = store.build_currency_context_for_instruments([instrument])
    return quote_notional_usd(qty, mark, spec, ctx.fx_rates.quote_per_usd)


def _spot_crypto_buying_power(account: dict, instrument) -> Decimal:
    cash = Decimal(str(account.get("cash") or account.get("equity") or account["starting_cash"]))
    spot_raw = account.get("spot_crypto_cash")
    spot = Decimal(str(spot_raw)) if spot_raw is not None else cash
    from quantara_engine.broker.instruments import get_instrument_spec

    spec = get_instrument_spec(instrument.symbol.upper().replace("/", ""))
    if spec.asset_class == "crypto":
        return effective_spot_crypto_cash(cash=cash, spot_crypto_cash=spot)
    return cash


def _ensure_live_sim_account_ready(
    store: TradingStore,
    account: dict | None,
    account_slug: str = LIVE_SIM_10K_ACCOUNT_SLUG,
) -> dict | None:
    if not account:
        return account
    if account_slug == LIVE_SIM_10K_ACCOUNT_SLUG:
        repair_pristine_live_sim_spot_crypto_cash(store)
        return _account_row(store, account_slug)
    return account


def _live_sim_execution_route(store: TradingStore, account_slug: str, symbol: str, direction: str):
    """Canonical Live Sim execution product for sizing / pre-trade economics."""
    from quantara_engine.broker.accounts import LIVE_SIM_VENDOR_ACCOUNT_SLUGS
    from quantara_engine.broker.execution_model import ExecutionModelVersion
    from quantara_engine.broker.execution_product import route_execution_product
    from quantara_engine.live_sim.execution_routing import resolve_execution_model_for_slug

    # Vendor accounts always use realistic economics; legacy 10k uses DB model.
    if account_slug in LIVE_SIM_VENDOR_ACCOUNT_SLUGS:
        model = ExecutionModelVersion.REALISTIC_BROKER_V1
    else:
        model = resolve_execution_model_for_slug(store, account_slug)
    return route_execution_product(symbol, direction, execution_model=model)


def _live_sim_max_asset_leverage(
    instrument,
    *,
    account_slug: str,
    direction: str,
    store: TradingStore,
) -> Decimal:
    route = _live_sim_execution_route(store, account_slug, instrument.symbol, direction)
    return route.rules.max_leverage or Decimal("1")


def _remaining_sl_gate_budget(
    *,
    settings,
    equity: Decimal,
    open_risk,
    symbol: str,
) -> Decimal:
    """Hard remaining SL-risk capacity for symbol/total/(group) gates — no tolerance."""
    sym = symbol.upper().replace("/", "")
    rem_sym = equity * settings.max_symbol_sl_risk_pct / Decimal("100") - open_risk.by_symbol.get(
        sym, Decimal("0")
    )
    rem_tot = (
        equity * settings.max_total_open_sl_risk_pct / Decimal("100") - open_risk.total_sl_risk_usd
    )
    rem = min(rem_sym, rem_tot)
    if settings.concentration_mode == "ENFORCE":
        grp = symbol_risk_group(sym)
        if grp:
            rem_grp = (
                equity * settings.max_group_sl_risk_pct / Decimal("100")
                - open_risk.by_group.get(grp, Decimal("0"))
            )
            rem = min(rem, rem_grp)
    return max(Decimal("0"), rem)


def _execute_accepted_allocation(
    store: TradingStore,
    *,
    account_id: str,
    account: dict,
    account_slug: str,
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
    idem = live_sim_execution_idempotency_key(account_slug, canonical_key)
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
        account_slug=account_slug,
        stop_loss=sl if sl and sl > 0 else None,
        take_profit=take_profit,
    )

    if broker_res is None or not broker_res.accepted:
        from quantara_engine.broker.display import broker_reason_he

        reason_code = (
            broker_res.decision.rejection_reason.value
            if broker_res and broker_res.decision and broker_res.decision.rejection_reason
            else "broker_rejected"
        )
        detail = broker_reason_he(reason_code)
        mark_allocation_rejected(
            store,
            log_id,
            rejection_reason="BROKER_REJECTED",
            rejection_detail=detail,
        )
        return {
            "status": "rejected",
            "reason": "BROKER_REJECTED",
            "detail": detail,
            "broker_reason": reason_code,
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

    try:
        from quantara_engine.learning.activation import get_active_baseline
        from quantara_engine.learning.funnel import record_funnel_event
        from quantara_engine.learning.planned_vs_actual import record_planned_vs_actual

        baseline = get_active_baseline(store)
        baseline_id = baseline["id"] if baseline else None
        record_planned_vs_actual(
            store,
            baseline_id=baseline_id,
            source_type="live_sim",
            symbol=instrument.symbol,
            direction=direction,
            actual_fill_price=fill.fill_price,
            quantity=qty,
            planned_sl=sl,
            planned_tp=take_profit,
            signal_candle_close=entry_ref,
            planned_entry_ref=entry_ref,
            execution_candle_open=exec_candle.open,
            planned_risk_usd=expected_risk,
            equity_at_entry=equity,
            spread=getattr(fill, "spread_cost", None),
            slippage=getattr(fill, "slippage", None),
            fees=getattr(fill, "fees", None),
            timeframe=instance.timeframe,
            broker_account_id=account_id,
            live_sim_position_id=pos_id,
        )
        record_funnel_event(
            store,
            baseline_id=baseline_id,
            stage="fill",
            reason="live_sim_entry_fill",
            strategy_slug=strategy_slug,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            candle_timestamp=exec_candle.timestamp,
        )
        record_funnel_event(
            store,
            baseline_id=baseline_id,
            stage="position_opened",
            reason="live_sim_position_open",
            strategy_slug=strategy_slug,
            symbol=instrument.symbol,
            timeframe=instance.timeframe,
            direction=direction,
            candle_timestamp=exec_candle.timestamp,
            source_id=pos_id,
        )
    except Exception:
        pass

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

    svc = BrokerExecutionService(store, account_slug=account_slug)
    svc.mark_to_market({instrument.symbol.upper(): exec_candle.close}, at=exec_candle.timestamp)

    logger.info(
        "Live-sim entry %s %s qty=%s risk=$%s via %s",
        instrument.symbol,
        direction,
        qty,
        expected_risk,
        account_slug,
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
    timeframe = str(existing["timeframe"])
    meta = existing.get("metadata") or {}
    stored_exec_ts = meta.get("execution_candle_timestamp")
    exec_ts_hint = (
        datetime.fromisoformat(stored_exec_ts.replace("Z", "+00:00"))
        if isinstance(stored_exec_ts, str)
        else stored_exec_ts
    ) if stored_exec_ts else None
    exec_candle, exec_ts = resolve_execution_candle(
        candles,
        signal_ts,
        timeframe,
        execution_candle_timestamp=exec_ts_hint,
    )
    if exec_candle is None:
        return {"status": "queued", "log_id": existing["id"], "reason": "execution_candle_missing"}

    allowed, reject_reason = is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe=timeframe,
        now=execution_now,
    )
    if not allowed:
        if reject_reason and "execution_window_passed" in reject_reason:
            mark_allocation_expired(store, existing["id"], reason=reject_reason)
            return {"status": "expired", "log_id": existing["id"]}
        return {"status": "queued", "log_id": existing["id"]}

    from quantara_engine.broker.capability import check_entry_capability_for_account
    from quantara_engine.broker.display import broker_reason_he
    from quantara_engine.live_sim.candidate_log import mark_allocation_rejected

    runtime = resolve_live_sim_runtime_context(
        store, instrument.symbol, str(existing["direction"])
    )
    account_slug = runtime.account_slug
    if runtime.legacy_blocked or not runtime.account:
        mark_allocation_rejected(
            store,
            existing["id"],
            rejection_reason="BROKER_REJECTED",
            rejection_detail="legacy execution disabled",
        )
        return {"status": "rejected", "reason": "legacy_execution_disabled"}
    account = runtime.account
    account_id = runtime.account_id or account_id

    cap = check_entry_capability_for_account(
        account_slug,
        instrument,
        str(existing["direction"]),
        store=store,
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
    asset_ctx = _equal_asset_sizing_context(store, symbol=instrument.symbol, account=account)
    asset_cash_override: Decimal | None = None
    asset_row: dict | None = None
    broker_limits = load_risk_settings(dict(account))
    if asset_ctx:
        equity, asset_cash_override, routed_account_id, asset_row = asset_ctx
        account_id = routed_account_id
        routed = broker_account_row_by_id(store, routed_account_id)
        if routed:
            broker_limits = load_risk_settings(dict(routed))
            account = dict(routed)
            account_slug = str(routed["slug"])
    if asset_row:
        settings = load_asset_gate_settings(asset_row, broker_limits)
        settings = maybe_roll_asset_daily_start(
            store, asset_row["id"], settings, equity, execution_now
        )
        update_asset_high_water_mark(store, asset_row["id"], equity)
    else:
        settings = broker_limits
        settings = maybe_roll_daily_start(store, account_id, settings, equity, execution_now)
        update_high_water_mark(store, account_id, equity)

    open_risk = compute_open_sl_risk(store, account_id)
    dir_enum = Direction.LONG if existing["direction"] == "long" else Direction.SHORT
    direction = str(existing["direction"])
    sl = Decimal(str(existing["stop_loss"]))
    entry_ref = Decimal(str(existing["proposed_entry"]))
    target_risk = target_risk_for_equity(equity, settings)
    hard_max_risk = _remaining_sl_gate_budget(
        settings=settings, equity=equity, open_risk=open_risk, symbol=instrument.symbol
    )
    assumptions = execution_assumptions_for(instrument, exec_candle.close)
    from quantara_engine.broker.profile import profile_for_account_slug

    fx = store.build_currency_context_for_instruments([instrument]).fx_rates
    buying_power = (
        asset_cash_override
        if asset_cash_override is not None
        else _spot_crypto_buying_power(account, instrument)
    )
    product_route = _live_sim_execution_route(store, account_slug, instrument.symbol, direction)
    sizing = size_live_sim_entry(
        equity=equity,
        cash=buying_power,
        target_risk=target_risk,
        entry_reference=entry_ref,
        stop_loss=sl,
        direction=dir_enum,
        instrument=instrument,
        fx_rates=fx,
        execution_assumptions=assumptions,
        max_asset_leverage=_live_sim_max_asset_leverage(
            instrument, account_slug=account_slug, direction=direction, store=store
        ),
        current_asset_notional=_current_asset_notional_usd(
            store, account_id, instrument.symbol.upper().replace("/", "")
        ),
        product_rules=product_route.rules,
        hard_max_risk_usd=hard_max_risk,
    )
    if sizing.deny_reason or sizing.quantity <= 0:
        mark_allocation_rejected(
            store,
            existing["id"],
            rejection_reason=sizing.deny_reason or "MIN_QUANTITY",
            rejection_detail=REJECTION_HE.get(sizing.deny_reason or "MIN_QUANTITY", sizing.deny_reason or ""),
        )
        return {"status": "rejected", "reason": sizing.deny_reason or "MIN_QUANTITY"}

    mark = entry_mark_price_for_sizing(entry_ref, dir_enum, assumptions)
    profile = profile_for_account_slug(account_slug)
    spot_raw = Decimal(str(account.get("spot_crypto_cash") or "0"))
    cash_raw = Decimal(str(account.get("cash") or account.get("equity") or account["starting_cash"]))
    accepted, broker_reason = validate_live_sim_broker_pre_trade(
        equity=equity,
        cash=cash_raw,
        spot_crypto_cash=spot_raw,
        quantity=sizing.quantity,
        mark_price=mark,
        direction=dir_enum,
        instrument=instrument,
        profile=profile,
        fx_rates=fx,
        account_slug=account_slug,
        store=store,
    )
    if not accepted:
        from quantara_engine.broker.display import broker_reason_he

        detail = broker_reason_he(broker_reason or "broker_rejected")
        mark_allocation_rejected(
            store,
            existing["id"],
            rejection_reason="BROKER_REJECTED",
            rejection_detail=detail,
        )
        return {"status": "rejected", "reason": "BROKER_REJECTED", "detail": detail}

    qty = sizing.quantity
    expected_risk = sizing.expected_risk_usd

    return _execute_accepted_allocation(
        store,
        account_id=account_id,
        account=account,
        account_slug=account_slug,
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
        entry_ref=entry_ref,
        sl=sl,
        take_profit=Decimal(str(existing["take_profit"])) if existing.get("take_profit") else None,
        qty=qty,
        expected_risk=expected_risk,
        target_risk=target_risk,
        exec_candle=exec_candle,
        open_risk=open_risk,
        equity=equity,
        sizing_reason=sizing.sizing_reason or (existing.get("metadata") or {}).get("sizing_reason"),
    )


def resume_all_pending_live_sim_allocations(
    store: TradingStore,
    execution_now: datetime,
) -> dict:
    """Resume queued live-sim allocations independent of current signal evaluation."""
    report: dict = {
        "pending_found": 0,
        "not_ready": 0,
        "resumed": 0,
        "expired": 0,
        "broker_rejected": 0,
        "filled": 0,
    }
    from quantara_engine.live_sim.execution_routing import (
        broker_account_row_by_id,
        list_active_live_sim_broker_account_ids,
    )

    account_ids = list_active_live_sim_broker_account_ids(store)
    if not account_ids:
        return report

    report["expired"] = expire_stale_live_sim_allocations(store, execution_now)
    rows = store.session.execute(
        text(
            """
            SELECT id::text, canonical_opportunity_key, strategy_slug, symbol, timeframe,
                   broker_account_id::text
            FROM live_sim_allocation_log
            WHERE broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND accepted = TRUE
              AND broker_order_id IS NULL
              AND live_sim_position_id IS NULL
              AND COALESCE(metadata->>'pending_execution', 'false') = 'true'
              AND COALESCE(metadata->>'expired', 'false') = 'false'
            """
        ),
        {"aids": account_ids},
    ).mappings().all()

    report["pending_found"] = len(rows)
    for row in rows:
        try:
            account = broker_account_row_by_id(store, str(row["broker_account_id"]))
            if not account or not account.get("is_active"):
                continue
            account = dict(account)
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
            status = result.get("status")
            if status == "accepted":
                report["filled"] += 1
                report["resumed"] += 1
            elif status == "rejected":
                report["broker_rejected"] += 1
            elif status == "expired":
                report["expired"] += 1
            elif status in ("queued", "skipped"):
                report["not_ready"] += 1
        except Exception:
            logger.exception(
                "live_sim resume failed for allocation canonical=%s",
                row.get("canonical_opportunity_key"),
            )
            report["broker_rejected"] += 1
            try:
                store.session.rollback()
            except Exception:
                pass
    return report


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

    instance = entry["instance"]
    strategy_slug = instance.strategy_slug
    strategy_version = getattr(instance, "strategy_version", None) or "1.0.0"
    robot_label = ROBOT_LABELS.get(strategy_slug, strategy_slug)
    direction = "long" if signal.action == SignalAction.BUY else "short"
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT

    runtime = resolve_live_sim_runtime_context(store, instrument.symbol, direction)
    if runtime.legacy_blocked:
        return {"status": "skipped", "reason": "legacy_execution_disabled"}
    account = runtime.account
    account_slug = runtime.account_slug
    if not account or not account.get("is_active") or account.get("pending_owner_reset"):
        return {"status": "skipped", "reason": "account_inactive"}
    account = _ensure_live_sim_account_ready(store, account, account_slug)
    account_id = runtime.account_id or account["id"]

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

    from quantara_engine.trading.asset_trading_controls import (
        SCOPE_LIVE_SIM,
        allows_entries_for_symbol,
    )

    if not allows_entries_for_symbol(
        store.get_settings_dict(),
        scope=SCOPE_LIVE_SIM,
        symbol=instrument.symbol,
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
            rejection_reason="ASSET_TRADING_PAUSED",
            rejection_detail=REJECTION_HE.get("ASSET_TRADING_PAUSED", "Asset trading paused"),
        )
        return {"status": "rejected", "reason": "ASSET_TRADING_PAUSED"}

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
        account_slug,
        instrument,
        direction,
        store=store,
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
    asset_ctx = _equal_asset_sizing_context(store, symbol=instrument.symbol, account=account)
    asset_cash_override: Decimal | None = None
    asset_row: dict | None = None
    broker_limits = load_risk_settings(dict(account))
    if asset_ctx:
        equity, asset_cash_override, routed_account_id, asset_row = asset_ctx
        account_id = routed_account_id
        routed = broker_account_row_by_id(store, routed_account_id)
        if routed:
            broker_limits = load_risk_settings(dict(routed))
            account = dict(routed)
            account_slug = str(routed["slug"])
    if asset_row:
        settings = load_asset_gate_settings(asset_row, broker_limits)
        settings = maybe_roll_asset_daily_start(
            store, asset_row["id"], settings, equity, execution_now
        )
        update_asset_high_water_mark(store, asset_row["id"], equity)
    else:
        settings = broker_limits
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

    buying_power = (
        asset_cash_override
        if asset_cash_override is not None
        else _spot_crypto_buying_power(account, instrument)
    )
    sym_db = instrument.symbol.upper().replace("/", "")
    open_risk = compute_open_sl_risk(store, account_id)
    hard_max_risk = _remaining_sl_gate_budget(
        settings=settings, equity=equity, open_risk=open_risk, symbol=instrument.symbol
    )
    product_route = _live_sim_execution_route(store, account_slug, instrument.symbol, direction)
    sizing = size_live_sim_entry(
        equity=equity,
        cash=buying_power,
        target_risk=target_risk,
        entry_reference=entry_ref,
        stop_loss=sl,
        direction=dir_enum,
        instrument=instrument,
        fx_rates=ctx.fx_rates,
        execution_assumptions=assumptions,
        max_asset_leverage=_live_sim_max_asset_leverage(
            instrument, account_slug=account_slug, direction=direction, store=store
        ),
        current_asset_notional=_current_asset_notional_usd(store, account_id, sym_db),
        product_rules=product_route.rules,
        hard_max_risk_usd=hard_max_risk,
    )
    qty = sizing.quantity
    expected_risk = sizing.expected_risk_usd
    deny = sizing.deny_reason

    sym = instrument.symbol.upper().replace("/", "")
    grp = symbol_risk_group(sym)

    def _reject(reason: str, detail: str) -> dict:
        sym_pct = (
            float(
                (open_risk.by_symbol.get(sym, Decimal("0")) + (expected_risk or Decimal("0")))
                / equity
                * 100
            )
        ) if equity > 0 else 0.0
        grp_pct = (
            float(
                (open_risk.by_group.get(grp, Decimal("0")) + (expected_risk or Decimal("0")))
                / equity
                * 100
            )
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

    if asset_row:
        broker_equity = Decimal(str(account.get("equity") or account.get("starting_cash")))
        broker_dd = evaluate_drawdown_gate(
            equity=broker_equity,
            high_water_mark=broker_limits.high_water_mark,
            max_drawdown_gate_pct=broker_limits.max_drawdown_gate_pct,
            scope_label="broker drawdown",
        )
        if not broker_dd.allowed:
            return _reject(
                broker_dd.reason or "DRAWDOWN_GATE",
                broker_dd.detail or REJECTION_HE.get("DRAWDOWN_GATE", ""),
            )

    from quantara_engine.broker.reconciliation_orchestrator import is_broker_execution_allowed
    from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk
    from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

    if not is_broker_execution_allowed(store, account_slug):
        return _reject("BROKER_HALTED", REJECTION_HE.get("BROKER_HALTED", "broker halted"))

    global_verdict = evaluate_owner_global_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol=instrument.symbol,
        incremental_sl_risk_usd=expected_risk or Decimal("0"),
        broker_local_allowed=True,
    )
    if not global_verdict.allowed:
        return _reject(
            global_verdict.reason or "OWNER_GLOBAL_RISK",
            REJECTION_HE.get(global_verdict.reason or "", global_verdict.reason or ""),
        )

    from quantara_engine.broker.instruments import get_instrument_spec
    from quantara_engine.broker.margin import initial_margin_for_notional, quote_notional_usd
    from quantara_engine.broker.profile import profile_for_account_slug
    from quantara_engine.owner_portfolio.asset_risk import evaluate_asset_envelope_risk

    mark = entry_mark_price_for_sizing(entry_ref, dir_enum, assumptions)
    profile = profile_for_account_slug(account_slug)
    spec = get_instrument_spec(instrument.symbol.upper().replace("/", ""))
    fx = ctx.fx_rates.quote_per_usd
    incremental_notional_usd = quote_notional_usd(
        Decimal(str(qty or 0)), mark, spec, fx
    )
    required_cash_usd = initial_margin_for_notional(
        incremental_notional_usd, product_route.rules
    )

    asset_verdict = evaluate_asset_envelope_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol=instrument.symbol,
        incremental_sl_risk_usd=expected_risk or Decimal("0"),
        incremental_notional_usd=incremental_notional_usd,
        required_cash_usd=required_cash_usd,
    )
    if not asset_verdict.allowed:
        return _reject(
            asset_verdict.reason or "ASSET_ENVELOPE_RISK",
            REJECTION_HE.get(asset_verdict.reason or "", asset_verdict.reason or ""),
        )

    spot_raw = Decimal(str(account.get("spot_crypto_cash") or "0"))
    cash_raw = Decimal(str(account.get("cash") or account.get("equity") or account["starting_cash"]))
    broker_ok, broker_reason = validate_live_sim_broker_pre_trade(
        equity=equity,
        cash=cash_raw,
        spot_crypto_cash=spot_raw,
        quantity=qty,
        mark_price=mark,
        direction=dir_enum,
        instrument=instrument,
        profile=profile,
        fx_rates=ctx.fx_rates,
        account_slug=account_slug,
        store=store,
    )
    if not broker_ok:
        from quantara_engine.broker.display import broker_reason_he

        return _reject(
            "BROKER_REJECTED",
            broker_reason_he(broker_reason or "broker_rejected"),
        )

    exec_ts = next_execution_timestamp(candle.timestamp, instance.timeframe)
    pending_meta = {
        "pending_execution": True,
        "lifecycle_state": "pending_execution",
        "sizing_reason": sizing.sizing_reason,
        "execution_candle_timestamp": exec_ts.isoformat(),
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

    exec_candle, exec_ts = resolve_execution_candle(
        candles,
        candle.timestamp,
        instance.timeframe,
        execution_candle_timestamp=exec_ts,
    )
    allowed, reject_reason = (
        is_execution_candle_ready(
            signal_candle_timestamp=candle.timestamp,
            execution_candle_timestamp=exec_ts,
            timeframe=instance.timeframe,
            now=execution_now,
        )
        if exec_candle is not None
        else (False, "execution_candle_missing")
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
        account_slug=account_slug,
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
        return exec_result
    return exec_result
