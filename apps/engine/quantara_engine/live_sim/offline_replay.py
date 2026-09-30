"""Deterministic Live Sim V1 vs V2 offline replay (read-only against production DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Literal

from sqlalchemy import text

from quantara_engine.domain.types import Direction
from quantara_engine.live_sim.allocator import (
    _current_asset_notional_usd,
    _live_sim_execution_route,
    _live_sim_max_asset_leverage,
    _remaining_sl_gate_budget,
    _spot_crypto_buying_power,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.live_sim.risk_policy import (
    OpenRiskSnapshot,
    evaluate_entry_gates,
    load_risk_settings,
    symbol_risk_group,
    target_risk_for_equity,
)
from quantara_engine.live_sim.sizing import size_live_sim_entry
from quantara_engine.live_sim.v2_policy import evaluate_live_sim_v2_policy
from quantara_engine.owner_portfolio.asset_allocation import (
    asset_equity,
    get_asset_allocation_row,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.asset_ledger import asset_available_cash
from quantara_engine.owner_portfolio.asset_risk import evaluate_asset_envelope_risk
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore
from quantara_engine.live_sim.asset_gate_settings import load_asset_gate_settings
from quantara_engine.live_sim.execution_routing import broker_account_row_by_id, list_active_live_sim_broker_account_ids

ReplayMode = Literal["v1", "v2"]


@dataclass
class SimTradeRecord:
    canonical_key: str
    strategy_slug: str
    symbol: str
    timeframe: str
    direction: str
    signal_ts: datetime
    accepted: bool
    rejection_reason: str | None
    target_risk_pct: float | None
    target_risk_usd: float | None
    raw_qty: float | None
    final_qty: float | None
    planned_sl_risk_usd: float | None
    planned_sl_risk_pct: float | None
    binding_constraint: str | None
    simulated_pnl_usd: float | None = None
    historical_pnl_usd: float | None = None
    live_sim_position_id: str | None = None


@dataclass
class ReplayState:
    """Chronological open SL risk for offline gate simulation."""

    open_by_symbol: dict[str, Decimal] = field(default_factory=dict)
    open_by_group: dict[str, Decimal] = field(default_factory=dict)
    total: Decimal = Decimal("0")

    def snapshot(self) -> OpenRiskSnapshot:
        return OpenRiskSnapshot(
            total_sl_risk_usd=self.total,
            by_symbol=dict(self.open_by_symbol),
            by_group=dict(self.open_by_group),
        )

    def add(self, symbol: str, risk: Decimal) -> None:
        sym = symbol.upper().replace("/", "")
        self.open_by_symbol[sym] = self.open_by_symbol.get(sym, Decimal("0")) + risk
        grp = symbol_risk_group(sym)
        if grp:
            self.open_by_group[grp] = self.open_by_group.get(grp, Decimal("0")) + risk
        self.total += risk

    def remove(self, symbol: str, risk: Decimal) -> None:
        sym = symbol.upper().replace("/", "")
        self.open_by_symbol[sym] = max(Decimal("0"), self.open_by_symbol.get(sym, Decimal("0")) - risk)
        grp = symbol_risk_group(sym)
        if grp:
            self.open_by_group[grp] = max(
                Decimal("0"), self.open_by_group.get(grp, Decimal("0")) - risk
            )
        self.total = max(Decimal("0"), self.total - risk)


def _equal_asset_v1_context(
    store: TradingStore, *, symbol: str, account: dict
) -> tuple[Decimal, Decimal, str, dict] | None:
    if not is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        return None
    row = get_asset_allocation_row(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol=symbol)
    if not row or not row.get("enabled"):
        return None
    equity = asset_equity(row)
    cash = asset_available_cash(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol=symbol)
    broker_id = row.get("broker_account_id") or account["id"]
    return equity, cash, str(broker_id), row


_OWNER_EQ_CACHE: Decimal | None = None


def _owner_equity_at(store: TradingStore, ts: datetime) -> Decimal:
    global _OWNER_EQ_CACHE
    if _OWNER_EQ_CACHE is not None:
        return _OWNER_EQ_CACHE
    from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio

    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    _OWNER_EQ_CACHE = Decimal(str(owner.total_equity if owner else 10000))
    return _OWNER_EQ_CACHE


def _resolve_economics(
    store: TradingStore,
    *,
    mode: ReplayMode,
    symbol: str,
    account: dict,
    account_id: str,
    account_slug: str,
    signal_ts: datetime,
    replay_state: ReplayState,
) -> dict[str, Any]:
    broker_limits = load_risk_settings(dict(account))
    asset_row: dict | None = None
    risk_equity: Decimal
    leverage_equity: Decimal
    buying_power: Decimal
    soft_pool = False

    v1_ctx = _equal_asset_v1_context(store, symbol=symbol, account=account)
    if v1_ctx:
        asset_equity_val, asset_cash, routed_id, asset_row = v1_ctx
        account_id = routed_id
        routed = broker_account_row_by_id(store, account_id)
        if routed:
            account = dict(routed)
            account_slug = str(routed["slug"])
            broker_limits = load_risk_settings(account)

    instrument = store.get_instrument_by_symbol(symbol.upper().replace("/", ""))
    if instrument is None:
        raise ValueError(f"missing instrument {symbol}")

    if mode == "v1":
        if v1_ctx:
            risk_equity = asset_equity_val
            leverage_equity = Decimal(str(account.get("equity") or account.get("starting_cash") or 0))
            buying_power = asset_cash
        else:
            risk_equity = Decimal(str(account.get("equity") or account.get("starting_cash") or 0))
            leverage_equity = risk_equity
            buying_power = _spot_crypto_buying_power(account, instrument)
        open_risk = replay_state.snapshot()
    else:
        owner_eq = _owner_equity_at(store, signal_ts)
        risk_equity = owner_eq
        leverage_equity = Decimal(str(account.get("equity") or account.get("starting_cash") or 0))
        buying_power = _spot_crypto_buying_power(account, instrument)
        if buying_power <= 0:
            buying_power = Decimal(str(account.get("cash") or leverage_equity))
        open_risk = replay_state.snapshot()
        soft_pool = is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG)

    if asset_row:
        settings = load_asset_gate_settings(asset_row, broker_limits)
    else:
        settings = broker_limits

    target_risk = target_risk_for_equity(risk_equity, settings)
    hard_max_risk = _remaining_sl_gate_budget(
        settings=settings,
        equity=risk_equity,
        open_risk=open_risk,
        symbol=symbol,
    )

    return {
        "account": account,
        "account_id": account_id,
        "account_slug": account_slug,
        "settings": settings,
        "asset_row": asset_row,
        "risk_equity": risk_equity,
        "leverage_equity": leverage_equity,
        "target_risk": target_risk,
        "open_risk": open_risk,
        "hard_max_risk": hard_max_risk,
        "buying_power": buying_power,
        "soft_shared_pool": soft_pool,
        "instrument": instrument,
    }


def _simulate_row(
    store: TradingStore,
    row: dict,
    *,
    mode: ReplayMode,
    replay_state: ReplayState,
    apply_v2_policy: bool,
) -> SimTradeRecord:
    slug = str(row["strategy_slug"])
    sym = str(row["symbol"])
    tf = str(row["timeframe"])
    if apply_v2_policy and mode == "v2":
        pol = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol=sym, timeframe=tf)
        if not pol.allowed:
            return SimTradeRecord(
                canonical_key=str(row["canonical_opportunity_key"]),
                strategy_slug=slug,
                symbol=sym,
                timeframe=tf,
                direction=str(row["direction"]),
                signal_ts=row["signal_candle_timestamp"],
                accepted=False,
                rejection_reason=pol.reason,
                target_risk_pct=None,
                target_risk_usd=None,
                raw_qty=None,
                final_qty=None,
                planned_sl_risk_usd=None,
                planned_sl_risk_pct=None,
                binding_constraint="POLICY",
                live_sim_position_id=row.get("live_sim_position_id"),
            )

    account_id = str(row["broker_account_id"])
    account = broker_account_row_by_id(store, account_id)
    if not account:
        return SimTradeRecord(
            canonical_key=str(row["canonical_opportunity_key"]),
            strategy_slug=slug,
            symbol=sym,
            timeframe=tf,
            direction=str(row["direction"]),
            signal_ts=row["signal_candle_timestamp"],
            accepted=False,
            rejection_reason="NO_ACCOUNT",
            target_risk_pct=None,
            target_risk_usd=None,
            raw_qty=None,
            final_qty=None,
            planned_sl_risk_usd=None,
            planned_sl_risk_pct=None,
            binding_constraint="OTHER",
        )
    account_slug = str(account["slug"])
    entry_ref = Decimal(str(row["proposed_entry"]))
    sl = Decimal(str(row["stop_loss"]))
    direction = str(row["direction"])
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT

    econ = _resolve_economics(
        store,
        mode=mode,
        symbol=sym,
        account=dict(account),
        account_id=account_id,
        account_slug=account_slug,
        signal_ts=row["signal_candle_timestamp"],
        replay_state=replay_state,
    )
    instrument = econ["instrument"]
    ctx = store.build_currency_context_for_instruments([instrument])
    assumptions = execution_assumptions_for(instrument, entry_ref)
    product_route = _live_sim_execution_route(store, econ["account_slug"], sym, direction)
    sizing = size_live_sim_entry(
        equity=econ["leverage_equity"],
        cash=econ["buying_power"],
        target_risk=econ["target_risk"],
        entry_reference=entry_ref,
        stop_loss=sl,
        direction=dir_enum,
        instrument=instrument,
        fx_rates=ctx.fx_rates,
        execution_assumptions=assumptions,
        max_asset_leverage=_live_sim_max_asset_leverage(
            instrument, account_slug=econ["account_slug"], direction=direction, store=store
        ),
        current_asset_notional=Decimal("0"),
        product_rules=product_route.rules,
        hard_max_risk_usd=econ["hard_max_risk"],
    )
    qty = sizing.quantity
    expected_risk = sizing.expected_risk_usd or Decimal("0")
    target_pct = float(econ["target_risk"] / econ["risk_equity"] * 100) if econ["risk_equity"] > 0 else None

    if sizing.deny_reason or qty <= 0:
        reason = sizing.deny_reason or "MIN_QUANTITY"
        return SimTradeRecord(
            canonical_key=str(row["canonical_opportunity_key"]),
            strategy_slug=slug,
            symbol=sym,
            timeframe=tf,
            direction=direction,
            signal_ts=row["signal_candle_timestamp"],
            accepted=False,
            rejection_reason=reason,
            target_risk_pct=target_pct,
            target_risk_usd=float(econ["target_risk"]),
            raw_qty=float(sizing.raw_qty_by_stop) if sizing.raw_qty_by_stop else None,
            final_qty=0.0,
            planned_sl_risk_usd=float(expected_risk),
            planned_sl_risk_pct=sizing.planned_sl_risk_pct,
            binding_constraint=sizing.binding_constraint,
            live_sim_position_id=row.get("live_sim_position_id"),
        )

    gate = evaluate_entry_gates(
        settings=econ["settings"],
        equity=econ["risk_equity"],
        realized_pnl_today=Decimal("0"),
        open_risk=econ["open_risk"],
        proposed_risk_usd=expected_risk,
        symbol=sym,
    )
    if not gate.allowed:  # GateResult
        return SimTradeRecord(
            canonical_key=str(row["canonical_opportunity_key"]),
            strategy_slug=slug,
            symbol=sym,
            timeframe=tf,
            direction=direction,
            signal_ts=row["signal_candle_timestamp"],
            accepted=False,
            rejection_reason=gate.reason or "RISK_LIMIT",
            target_risk_pct=target_pct,
            target_risk_usd=float(econ["target_risk"]),
            raw_qty=float(sizing.raw_qty_by_stop) if sizing.raw_qty_by_stop else None,
            final_qty=float(qty),
            planned_sl_risk_usd=float(expected_risk),
            planned_sl_risk_pct=sizing.planned_sl_risk_pct,
            binding_constraint="OWNER_LIMIT",
            live_sim_position_id=row.get("live_sim_position_id"),
        )

    asset_verdict = evaluate_asset_envelope_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol=sym,
        incremental_sl_risk_usd=expected_risk,
        incremental_notional_usd=Decimal("0"),
        required_cash_usd=Decimal("0"),
        soft_shared_pool=econ["soft_shared_pool"],
        broker_cash_usd=Decimal(str(econ["account"].get("cash") or 0)),
    )
    if not asset_verdict.allowed:
        return SimTradeRecord(
            canonical_key=str(row["canonical_opportunity_key"]),
            strategy_slug=slug,
            symbol=sym,
            timeframe=tf,
            direction=direction,
            signal_ts=row["signal_candle_timestamp"],
            accepted=False,
            rejection_reason=asset_verdict.reason or "ASSET_EXPOSURE",
            target_risk_pct=target_pct,
            target_risk_usd=float(econ["target_risk"]),
            raw_qty=float(sizing.raw_qty_by_stop) if sizing.raw_qty_by_stop else None,
            final_qty=float(qty),
            planned_sl_risk_usd=float(expected_risk),
            planned_sl_risk_pct=sizing.planned_sl_risk_pct,
            binding_constraint="ASSET_LIMIT",
            live_sim_position_id=row.get("live_sim_position_id"),
        )

    return SimTradeRecord(
        canonical_key=str(row["canonical_opportunity_key"]),
        strategy_slug=slug,
        symbol=sym,
        timeframe=tf,
        direction=direction,
        signal_ts=row["signal_candle_timestamp"],
        accepted=True,
        rejection_reason=None,
        target_risk_pct=target_pct,
        target_risk_usd=float(econ["target_risk"]),
        raw_qty=float(sizing.raw_qty_by_stop) if sizing.raw_qty_by_stop else None,
        final_qty=float(qty),
        planned_sl_risk_usd=float(expected_risk),
        planned_sl_risk_pct=sizing.planned_sl_risk_pct,
        binding_constraint=sizing.binding_constraint or "NONE",
        live_sim_position_id=row.get("live_sim_position_id"),
    )


def _load_opportunity_stream(
    store: TradingStore, *, anchor: datetime, broker_account_ids: list[str]
) -> list[dict]:
    """One final decision row per canonical opportunity since anchor (clean Live Sim stream)."""
    rows = store.session.execute(
        text(
            """
            WITH closed_pos AS (
              SELECT id::text AS pid FROM live_sim_positions
              WHERE broker_account_id = ANY(CAST(:ids AS uuid[]))
                AND status = 'closed' AND closed_at >= :anchor
            )
            SELECT DISTINCT ON (l.canonical_opportunity_key)
                   l.id::text, l.broker_account_id::text, l.canonical_opportunity_key,
                   l.strategy_slug, l.symbol, l.timeframe, l.direction::text,
                   l.signal_candle_timestamp, l.proposed_entry, l.stop_loss,
                   l.accepted, l.rejection_reason, l.live_sim_position_id::text,
                   l.calculated_risk_usd, l.calculated_quantity, l.created_at
            FROM live_sim_allocation_log l
            WHERE l.created_at >= :anchor
              AND l.stop_loss IS NOT NULL
              AND l.proposed_entry IS NOT NULL
              AND l.broker_account_id = ANY(CAST(:ids AS uuid[]))
              AND (
                l.live_sim_position_id::text IN (SELECT pid FROM closed_pos)
                OR (
                  l.robot_label = 'Robot B'
                  AND l.rejection_reason = 'MIN_QUANTITY_EXCEEDS_RISK_BUDGET'
                )
                OR l.strategy_slug IN ('volatility-squeeze', 'momentum-continuation')
              )
            ORDER BY l.canonical_opportunity_key, l.created_at DESC
            """
        ),
        {"anchor": anchor, "ids": broker_account_ids},
    ).mappings().all()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (r["signal_candle_timestamp"], r["created_at"]))
    return out


def _position_pnl_map(store: TradingStore, *, anchor: datetime, broker_account_ids: list[str]) -> dict[str, float]:
    rows = store.session.execute(
        text(
            """
            SELECT p.id::text AS pid,
              (
                SELECT COALESCE(SUM(l.realized_pnl), 0)
                FROM broker_attribution_ledger l
                WHERE l.strategy_position_id = p.id AND l.exit_price IS NOT NULL
              ) AS pnl,
              p.planned_sl_risk_usd,
              p.opened_at,
              p.closed_at
            FROM live_sim_positions p
            WHERE p.broker_account_id = ANY(CAST(:ids AS uuid[]))
              AND p.status = 'closed'
              AND p.closed_at >= :anchor
            """
        ),
        {"anchor": anchor, "ids": broker_account_ids},
    ).mappings().all()
    return {str(r["pid"]): float(r["pnl"] or 0) for r in rows}


def _attach_pnl(
    record: SimTradeRecord,
    *,
    hist_pnl: float | None,
    hist_risk: float | None,
) -> SimTradeRecord:
    record.historical_pnl_usd = hist_pnl
    if record.accepted and hist_pnl is not None and hist_risk and hist_risk > 0 and record.planned_sl_risk_usd:
        scale = record.planned_sl_risk_usd / hist_risk
        record.simulated_pnl_usd = hist_pnl * scale
    elif record.accepted and hist_pnl is not None:
        record.simulated_pnl_usd = hist_pnl
    else:
        record.simulated_pnl_usd = None
    return record


def _metrics(trades: list[SimTradeRecord], *, start_equity: float) -> dict[str, Any]:
    executed = [t for t in trades if t.accepted and t.simulated_pnl_usd is not None]
    risks = [t.planned_sl_risk_pct for t in trades if t.accepted and t.planned_sl_risk_pct is not None]
    pnls = [t.simulated_pnl_usd for t in executed]
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    pf = wins / abs(losses) if losses else None
    equity = start_equity
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100)
    return {
        "trades": len(executed),
        "net_pnl": round(sum(pnls), 2),
        "return_pct": round(sum(pnls) / start_equity * 100, 3) if start_equity else None,
        "pf": round(pf, 3) if pf is not None else None,
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "avg_planned_risk_pct": round(sum(risks) / len(risks), 4) if risks else None,
        "median_planned_risk_pct": round(float(median(risks)), 4) if risks else None,
        "largest_loss": round(min(pnls), 2) if pnls else None,
        "largest_win": round(max(pnls), 2) if pnls else None,
    }


def _load_closed_position_stream(
    store: TradingStore, *, anchor: datetime, broker_account_ids: list[str]
) -> list[dict]:
    """Accepted allocation row tied to each clean-window closed Live Sim position."""
    rows = store.session.execute(
        text(
            """
            SELECT l.id::text, l.broker_account_id::text, l.canonical_opportunity_key,
                   l.strategy_slug, l.symbol, l.timeframe, l.direction::text,
                   l.signal_candle_timestamp, l.proposed_entry, l.stop_loss,
                   l.accepted, l.rejection_reason, l.live_sim_position_id::text,
                   l.calculated_risk_usd, l.calculated_quantity, l.created_at
            FROM live_sim_positions p
            JOIN live_sim_allocation_log l ON l.live_sim_position_id = p.id AND l.accepted = TRUE
            WHERE p.broker_account_id = ANY(CAST(:ids AS uuid[]))
              AND p.status = 'closed'
              AND p.closed_at >= :anchor
            ORDER BY p.opened_at ASC
            """
        ),
        {"anchor": anchor, "ids": broker_account_ids},
    ).mappings().all()
    return [dict(r) for r in rows]


def run_clean_window_replay(
    store: TradingStore,
    *,
    anchor: datetime,
) -> dict[str, Any]:
    aids = list_active_live_sim_broker_account_ids(store)
    stream = _load_closed_position_stream(store, anchor=anchor, broker_account_ids=aids)
    funnel_stream = _load_opportunity_stream(store, anchor=anchor, broker_account_ids=aids)
    pnl_by_pos = _position_pnl_map(store, anchor=anchor, broker_account_ids=aids)

    hist_risk_by_pos = {
        str(r["pid"]): float(r["planned_sl_risk_usd"] or 0)
        for r in store.session.execute(
            text(
                """
                SELECT id::text AS pid, planned_sl_risk_usd
                FROM live_sim_positions
                WHERE broker_account_id = ANY(CAST(:ids AS uuid[]))
                  AND status = 'closed' AND closed_at >= :anchor
                """
            ),
            {"anchor": anchor, "ids": aids},
        ).mappings()
    }

    v1_state = ReplayState()
    v2_state = ReplayState()
    v2_size_state = ReplayState()
    v1_trades: list[SimTradeRecord] = []
    v2_trades: list[SimTradeRecord] = []
    v2_policy_v1_size: list[SimTradeRecord] = []

    for row in stream:
        pid = row.get("live_sim_position_id")
        hist_pnl = pnl_by_pos.get(pid) if pid else None
        hist_risk = hist_risk_by_pos.get(pid) if pid else None

        r1 = _simulate_row(store, row, mode="v1", replay_state=v1_state, apply_v2_policy=False)
        r1 = _attach_pnl(r1, hist_pnl=hist_pnl, hist_risk=hist_risk)
        v1_trades.append(r1)

        r2 = _simulate_row(store, row, mode="v2", replay_state=v2_state, apply_v2_policy=True)
        r2 = _attach_pnl(r2, hist_pnl=hist_pnl, hist_risk=hist_risk)
        v2_trades.append(r2)

        r2s = _simulate_row(store, row, mode="v2", replay_state=v2_size_state, apply_v2_policy=False)
        r2s = _attach_pnl(r2s, hist_pnl=hist_pnl, hist_risk=hist_risk)
        v2_policy_v1_size.append(r2s)

        if r1.accepted and r1.planned_sl_risk_usd:
            v1_state.add(r1.symbol, Decimal(str(r1.planned_sl_risk_usd)))
        if r2.accepted and r2.planned_sl_risk_usd:
            v2_state.add(r2.symbol, Decimal(str(r2.planned_sl_risk_usd)))
        if r2s.accepted and r2s.planned_sl_risk_usd:
            v2_size_state.add(r2s.symbol, Decimal(str(r2s.planned_sl_risk_usd)))

    start_eq = float(_owner_equity_at(store, anchor))
    return {
        "closed_position_opportunities": len(stream),
        "extended_funnel_opportunities": len(funnel_stream),
        "start_equity": start_eq,
        "v1": _metrics(v1_trades, start_equity=start_eq),
        "v2": _metrics(v2_trades, start_equity=start_eq),
        "v2_sizing_only_with_v1_policy": _metrics(v2_policy_v1_size, start_equity=start_eq),
        "v1_trades_detail": [t.__dict__ for t in v1_trades if t.live_sim_position_id],
        "v2_trades_detail": [t.__dict__ for t in v2_trades if t.accepted][:50],
    }


def robot_b_min_qty_replay(store: TradingStore, *, anchor: datetime) -> dict[str, Any]:
    rows = store.session.execute(
        text(
            """
            SELECT DISTINCT ON (canonical_opportunity_key)
                   broker_account_id::text, canonical_opportunity_key,
                   strategy_slug, symbol, timeframe, direction::text,
                   signal_candle_timestamp, proposed_entry, stop_loss,
                   rejection_reason, live_sim_position_id::text, created_at
            FROM live_sim_allocation_log
            WHERE created_at >= :anchor
              AND robot_label = 'Robot B'
              AND rejection_reason = 'MIN_QUANTITY_EXCEEDS_RISK_BUDGET'
              AND stop_loss IS NOT NULL
            ORDER BY canonical_opportunity_key, created_at DESC
            """
        ),
        {"anchor": anchor},
    ).mappings().all()
    state = ReplayState()
    old_valid = 0
    new_valid = 0
    samples: list[dict] = []
    for row in rows:
        full = dict(row)
        full["broker_account_id"] = row["broker_account_id"]
        v1 = _simulate_row(store, full, mode="v1", replay_state=state, apply_v2_policy=False)
        v2 = _simulate_row(store, full, mode="v2", replay_state=ReplayState(), apply_v2_policy=True)
        if v1.accepted:
            old_valid += 1
        if v2.accepted:
            new_valid += 1
            if len(samples) < 5:
                samples.append(
                    {
                        "canonical": full["canonical_opportunity_key"],
                        "planned_sl_risk_usd": v2.planned_sl_risk_usd,
                        "final_qty": v2.final_qty,
                        "binding": v2.binding_constraint,
                    }
                )
    return {
        "min_qty_reject_rows": len(rows),
        "historical_valid": 0,
        "v1_replay_valid": old_valid,
        "v2_replay_valid": new_valid,
        "samples": samples,
    }
