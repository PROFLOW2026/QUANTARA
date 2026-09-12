"""Physical account risk concentration — symbol and group aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantara_engine.competition.robot_registry import ROBOT_LABELS
from quantara_engine.persistence.batch_summary import (
    _batch_instruments_by_id,
    _position_exposure_usd,
    _resolve_position_open_risk,
    batch_entry_actual_risk_by_position,
    batch_portfolio_equity,
)
from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates

RISK_GROUPS: dict[str, tuple[str, ...]] = {
    "CRYPTO_RISK": ("BTCUSD", "ETHUSD", "COIN"),
    "US_HIGH_BETA": ("NVDA", "AMD", "TSLA", "COIN"),
    "SEMICONDUCTORS": ("NVDA", "AMD"),
    "FX": ("GBPJPY",),
    "METALS": ("XAUUSD",),
}

CONCENTRATION_MODE_OBSERVE = "OBSERVE"
CONCENTRATION_MODE_ENFORCE = "ENFORCE"


@dataclass
class SymbolConcentration:
    symbol: str
    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal
    long_exposure_usd: Decimal
    short_exposure_usd: Decimal
    sl_risk_usd: Decimal
    sl_risk_pct: float
    portfolio_count: int
    robot_count: int
    robots: list[str] = field(default_factory=list)


@dataclass
class GroupConcentration:
    group: str
    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal
    sl_risk_usd: Decimal
    sl_risk_pct: float
    assets: list[str] = field(default_factory=list)
    robot_count: int = 0


def build_physical_concentration(store, *, broker_equity: Decimal) -> dict[str, Any]:
    from quantara_engine.competition.paper_run import position_scope_clause
    from quantara_engine.models.enums import PositionStatus as OrmPositionStatus
    from quantara_engine.models.trading import Position as OrmPosition
    from sqlalchemy import select

    _, _, entries = store.list_all_competition_entries()
    portfolio_ids = [e["portfolio"].id for e in entries]
    instance_robot: dict[str, str] = {}
    portfolio_robots: dict[str, set[str]] = {}
    for entry in entries:
        slug = entry["instance"].strategy_slug
        robot = ROBOT_LABELS.get(slug, slug)
        instance_robot[entry["instance"].id] = robot
        portfolio_robots.setdefault(entry["portfolio"].id, set()).add(robot)

    import uuid

    portfolio_uuids = [uuid.UUID(pid) for pid in portfolio_ids]
    open_rows = store.session.execute(
        select(
            OrmPosition.id,
            OrmPosition.portfolio_id,
            OrmPosition.strategy_instance_id,
            OrmPosition.instrument_id,
            OrmPosition.quantity,
            OrmPosition.current_price,
            OrmPosition.entry_price,
            OrmPosition.stop_loss,
            OrmPosition.direction,
        ).where(
            OrmPosition.portfolio_id.in_(portfolio_uuids),
            OrmPosition.status == OrmPositionStatus.OPEN,
            position_scope_clause(store),
        )
    ).all()

    if not open_rows:
        return {
            "mode": CONCENTRATION_MODE_OBSERVE,
            "broker_equity_usd": float(broker_equity),
            "symbols": [],
            "groups": [],
        }

    position_ids = [str(row[0]) for row in open_rows]
    instrument_ids = list({str(row[3]) for row in open_rows})
    instruments_by_id = _batch_instruments_by_id(store, instrument_ids)
    symbol_by_id = {iid: inst.symbol for iid, inst in instruments_by_id.items() if inst}
    fx_rates = resolve_dashboard_fx_rates(
        store, quote_currencies_for_instruments(instruments_by_id.values())
    )
    intent_risk = batch_entry_actual_risk_by_position(store, position_ids)

    by_symbol: dict[str, dict[str, Any]] = {}
    for (
        position_id,
        portfolio_id,
        strategy_instance_id,
        instrument_id,
        quantity,
        current_price,
        entry_price,
        stop_loss,
        direction,
    ) in open_rows:
        iid = str(instrument_id)
        symbol = symbol_by_id.get(iid, iid)
        inst = instruments_by_id.get(iid)
        mark = Decimal(str(current_price or entry_price or 0))
        qty = Decimal(str(quantity or 0))
        signed_qty = qty if str(direction).lower() == "long" else -qty
        usd = _position_exposure_usd(quantity=qty, mark=mark, instrument=inst, fx_rates=fx_rates) or Decimal("0")
        signed_usd = usd if signed_qty >= 0 else -usd

        risk, _ = _resolve_position_open_risk(
            position_id=str(position_id),
            quantity=qty,
            direction=direction,
            entry_price=Decimal(str(entry_price or 0)),
            stop_loss=Decimal(str(stop_loss or 0)),
            instrument=inst,
            intent_risk=intent_risk.get(str(position_id)),
            fx_rates=fx_rates,
        )
        risk_usd = risk or Decimal("0")

        bucket = by_symbol.setdefault(
            symbol,
            {
                "gross": Decimal("0"),
                "net": Decimal("0"),
                "long": Decimal("0"),
                "short": Decimal("0"),
                "sl_risk": Decimal("0"),
                "portfolios": set(),
                "robots": set(),
            },
        )
        bucket["gross"] += usd
        bucket["net"] += signed_usd
        if signed_qty >= 0:
            bucket["long"] += usd
        else:
            bucket["short"] += usd
        bucket["sl_risk"] += risk_usd
        bucket["portfolios"].add(str(portfolio_id))
        bucket["robots"].add(instance_robot.get(str(strategy_instance_id), "unknown"))

    symbols: list[SymbolConcentration] = []
    for symbol, data in sorted(by_symbol.items()):
        sl_pct = float(data["sl_risk"] / broker_equity * 100) if broker_equity > 0 else 0.0
        symbols.append(
            SymbolConcentration(
                symbol=symbol,
                gross_exposure_usd=data["gross"],
                net_exposure_usd=data["net"],
                long_exposure_usd=data["long"],
                short_exposure_usd=data["short"],
                sl_risk_usd=data["sl_risk"],
                sl_risk_pct=sl_pct,
                portfolio_count=len(data["portfolios"]),
                robot_count=len(data["robots"]),
                robots=sorted(data["robots"]),
            )
        )

    groups: list[GroupConcentration] = []
    for group_name, assets in RISK_GROUPS.items():
        gross = Decimal("0")
        net = Decimal("0")
        sl_risk = Decimal("0")
        present_assets: list[str] = []
        robots: set[str] = set()
        for asset in assets:
            row = by_symbol.get(asset)
            if not row:
                continue
            present_assets.append(asset)
            gross += row["gross"]
            net += row["net"]
            sl_risk += row["sl_risk"]
            robots.update(row["robots"])
        if not present_assets:
            continue
        sl_pct = float(sl_risk / broker_equity * 100) if broker_equity > 0 else 0.0
        groups.append(
            GroupConcentration(
                group=group_name,
                gross_exposure_usd=gross,
                net_exposure_usd=net,
                sl_risk_usd=sl_risk,
                sl_risk_pct=sl_pct,
                assets=present_assets,
                robot_count=len(robots),
            )
        )

    return {
        "mode": CONCENTRATION_MODE_OBSERVE,
        "broker_equity_usd": float(broker_equity),
        "symbols": [
            {
                "symbol": s.symbol,
                "gross_exposure_usd": float(s.gross_exposure_usd),
                "net_exposure_usd": float(s.net_exposure_usd),
                "long_exposure_usd": float(s.long_exposure_usd),
                "short_exposure_usd": float(s.short_exposure_usd),
                "sl_risk_usd": float(s.sl_risk_usd),
                "sl_risk_pct": s.sl_risk_pct,
                "portfolio_count": s.portfolio_count,
                "robot_count": s.robot_count,
                "robots": s.robots,
            }
            for s in symbols
        ],
        "groups": [
            {
                "group": g.group,
                "gross_exposure_usd": float(g.gross_exposure_usd),
                "net_exposure_usd": float(g.net_exposure_usd),
                "sl_risk_usd": float(g.sl_risk_usd),
                "sl_risk_pct": g.sl_risk_pct,
                "assets": g.assets,
                "robot_count": g.robot_count,
            }
            for g in groups
        ],
    }
