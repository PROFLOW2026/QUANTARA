#!/usr/bin/env python3
"""Calculate competition risk differentiation for a representative Gold signal."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE_PATH))

from quantara_engine.competition.constants import COMPETITION_PORTFOLIOS  # noqa: E402
from quantara_engine.competition.leverage import compute_sizing_metrics  # noqa: E402
from quantara_engine.domain.types import Instrument, Mode, Portfolio  # noqa: E402
from quantara_engine.risk.profiles import get_risk_profile  # noqa: E402
from quantara_engine.risk.sizing import (  # noqa: E402
    compute_position_size,
    compute_target_risk_amount,
    round_quantity,
)

# Representative signal from legacy Paper Main open SHORT (Gold Trend Pullback)
ENTRY = Decimal("4404.51")
STOP = Decimal("4419.74")
SL_DISTANCE = abs(ENTRY - STOP)
EQUITY = Decimal("2000")
MARK = ENTRY

INSTRUMENT = Instrument(
    id="xauusd",
    symbol="XAUUSD",
    name="Gold",
    asset_class="commodity",
    base_currency="XAU",
    quote_currency="USD",
    pip_size=Decimal("0.01"),
    contract_size=Decimal("100"),
    price_tick_size=Decimal("0.01"),
    quantity_step=Decimal("0.01"),
    min_quantity=Decimal("0.01"),
)

TIERS = [
    ("0.25%", "very_conservative", Decimal("0.25")),
    ("0.50%", "conservative", Decimal("0.50")),
    ("1.00%", "balanced", Decimal("1.00")),
    ("1.50%", "aggressive", Decimal("1.50")),
    ("2.00%", "very_aggressive", Decimal("2.00")),
]


def _portfolio(name: str) -> Portfolio:
    return Portfolio(
        id="comp",
        name=name,
        mode=Mode.PAPER,
        initial_capital=EQUITY,
        balance=EQUITY,
        equity=EQUITY,
    )


def _required_row(slug: str, risk_pct: Decimal) -> dict:
    profile = get_risk_profile(slug)
    portfolio = _portfolio(slug)
    target_risk = compute_target_risk_amount(portfolio, profile)
    req_qty = target_risk / SL_DISTANCE
    req_qty_rounded = round_quantity(req_qty, INSTRUMENT.quantity_step)
    notional = req_qty_rounded * MARK
    exposure_pct = notional / EQUITY * Decimal("100")
    return {
        "target_pct": risk_pct,
        "target_risk": target_risk,
        "req_qty": req_qty_rounded,
        "req_notional": notional.quantize(Decimal("0.01")),
        "req_exposure_pct": exposure_pct.quantize(Decimal("0.01")),
    }


def _size_row(slug: str, virtual: bool) -> dict:
    profile = get_risk_profile(slug)
    portfolio = _portfolio(slug)
    qty, target, actual, deny = compute_position_size(
        portfolio=portfolio,
        risk_profile=profile,
        instrument=INSTRUMENT,
        entry_reference=ENTRY,
        stop_loss=STOP,
        direction="short",
        open_positions=[],
        mark_price=MARK,
        allow_virtual_leverage=virtual,
    )
    metrics = compute_sizing_metrics(qty, MARK, EQUITY, target, actual)
    return {
        "qty": qty,
        "actual_risk": actual,
        "actual_risk_pct": metrics["actual_risk_pct"],
        "exposure_pct": metrics["exposure_pct"],
        "virtual_leverage": metrics["virtual_leverage"],
        "deny": deny,
    }


def main() -> None:
    print("Representative entry =", ENTRY)
    print("Representative stop distance =", SL_DISTANCE)
    print()
    header = (
        f"{'Portfolio':<12} {'Target%':>8} {'ReqQty':>8} {'ReqNotional':>12} "
        f"{'ReqExp%':>8} {'ActQty@100%':>11} {'ActRisk%@100%':>14} "
        f"{'ActQty@virt':>11} {'ActRisk%@virt':>13} {'Lev':>6}"
    )
    print(header)
    print("-" * len(header))

    collapse = True
    capped_qtys: set[Decimal] = set()

    for label, slug, risk_pct in TIERS:
        req = _required_row(slug, risk_pct)
        capped = _size_row(slug, virtual=False)
        virt = _size_row(slug, virtual=True)
        capped_qtys.add(capped["qty"])
        print(
            f"{label:<12} {float(risk_pct):>7.2f}% {req['req_qty']:>8} "
            f"{req['req_notional']:>12} {req['req_exposure_pct']:>7.1f}% "
            f"{capped['qty']:>11} {capped['actual_risk_pct']:>13.4f}% "
            f"{virt['qty']:>11} {virt['actual_risk_pct']:>12.4f}% "
            f"{virt['virtual_leverage']:>5.2f}x"
        )

    if len(capped_qtys) <= 2:
        collapse = True

    differentiated_virt = len({(_size_row(s, True)["qty"]) for _, s, _ in TIERS}) == 5
    print()
    print("100% cap caused tier collapse =", "YES" if collapse else "NO")
    print("Risk tiers genuinely differentiated (virtual) =", "YES" if differentiated_virt else "NO")


if __name__ == "__main__":
    main()
