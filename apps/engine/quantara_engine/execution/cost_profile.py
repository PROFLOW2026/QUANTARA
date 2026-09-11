"""Instrument-aware paper execution cost profiles (spread in pips / absolute / bps)."""



from __future__ import annotations



from dataclasses import dataclass

from decimal import Decimal

from typing import Literal



from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument

from quantara_engine.execution.fill_calculator import calculate_fill_price



SpreadModel = Literal["pips", "absolute", "bps"]

SlippageModel = Literal["pct", "pips", "absolute"]





@dataclass(frozen=True)

class ExecutionCostProfile:

    """Canonical paper spread/slippage metadata per instrument."""



    spread_model: SpreadModel

    spread_pips: Decimal | None = None

    spread_absolute: Decimal | None = None

    spread_bps: Decimal | None = None

    slippage_model: SlippageModel = "pct"

    slippage_pips: Decimal | None = None

    slippage_absolute: Decimal | None = None

    slippage_pct: Decimal = Decimal("0.0001")



    def full_spread_quote(self, instrument: Instrument, reference_price: Decimal) -> Decimal:

        if self.spread_model == "pips":

            pip = instrument.pip_size or Decimal("0.01")

            pips = self.spread_pips or Decimal("0")

            return (pips * pip).quantize(Decimal("0.00000001"))

        if self.spread_model == "absolute":

            return (self.spread_absolute or Decimal("0")).quantize(Decimal("0.00000001"))

        bps = self.spread_bps or Decimal("0")

        return (reference_price * bps / Decimal("10000")).quantize(Decimal("0.00000001"))



    def slippage_per_side_quote(self, instrument: Instrument, reference_price: Decimal) -> Decimal:

        if self.slippage_model == "pips":

            pip = instrument.pip_size or Decimal("0.01")

            pips = self.slippage_pips or Decimal("0")

            return (pips * pip).quantize(Decimal("0.00000001"))

        if self.slippage_model == "absolute":

            return (self.slippage_absolute or Decimal("0")).quantize(Decimal("0.00000001"))

        return (self.slippage_pct * reference_price).quantize(Decimal("0.00000001"))



    def round_trip_spread_quote(self, instrument: Instrument, reference_price: Decimal) -> Decimal:

        return self.full_spread_quote(instrument, reference_price)



    def to_execution_assumptions(

        self, instrument: Instrument, reference_price: Decimal

    ) -> ExecutionAssumptions:

        slip_side = self.slippage_per_side_quote(instrument, reference_price)

        return ExecutionAssumptions(

            spread=self.full_spread_quote(instrument, reference_price),

            slippage_pct=self.slippage_pct,

            slippage_per_side=slip_side if self.slippage_model != "pct" else None,

            fee_rate=Decimal("0"),

            fill_timing="next_open",

        )





# Configurable paper defaults — single source of truth.

EXECUTION_COST_BY_SYMBOL: dict[str, ExecutionCostProfile] = {

    "GBPJPY": ExecutionCostProfile(

        spread_model="pips",

        spread_pips=Decimal("2.0"),

        slippage_model="pips",

        slippage_pips=Decimal("0.2"),

    ),

    "XAUUSD": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.30")),

    "BTCUSD": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.30")),

    "ETHUSD": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.30")),

    "NVDA": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.02")),

    "TSLA": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.02")),

    "AMD": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.02")),

    "COIN": ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.02")),

}



_DEFAULT_PROFILE = ExecutionCostProfile(spread_model="absolute", spread_absolute=Decimal("0.30"))



# Reference prices for audit / diagnostics (not used in live fills).

AUDIT_REFERENCE_PRICES: dict[str, Decimal] = {

    "BTCUSD": Decimal("60000"),

    "ETHUSD": Decimal("3500"),

    "XAUUSD": Decimal("4340"),

    "GBPJPY": Decimal("200"),

    "NVDA": Decimal("170"),

    "TSLA": Decimal("350"),

    "AMD": Decimal("160"),

    "COIN": Decimal("250"),

}





def get_execution_cost_profile(symbol: str) -> ExecutionCostProfile:

    return EXECUTION_COST_BY_SYMBOL.get(symbol.upper(), _DEFAULT_PROFILE)





def execution_assumptions_for(

    instrument: Instrument, reference_price: Decimal | None = None

) -> ExecutionAssumptions:

    ref = reference_price or Decimal("1")

    profile = get_execution_cost_profile(instrument.symbol)

    return profile.to_execution_assumptions(instrument, ref)





def describe_execution_cost(symbol: str, reference_price: Decimal) -> dict:

    """Summary for audit reports."""

    from quantara_engine.market_data.registry import get_asset



    asset = get_asset(symbol)

    pip = Decimal(str(asset.pip_size)) if asset else Decimal("0.01")

    inst = Instrument(

        id="",

        symbol=symbol,

        name=symbol,

        pip_size=pip,

    )

    profile = get_execution_cost_profile(symbol)

    assumptions = profile.to_execution_assumptions(inst, reference_price)

    entry = calculate_fill_price(Direction.LONG, "entry", reference_price, Decimal("1"), assumptions)

    exit_ = calculate_fill_price(Direction.LONG, "exit", reference_price, Decimal("1"), assumptions)

    rt_spread = profile.full_spread_quote(inst, reference_price)

    rt_slip = profile.slippage_per_side_quote(inst, reference_price) * 2

    return {

        "symbol": symbol,

        "spread_model": profile.spread_model,

        "spread_configured": str(rt_spread),

        "spread_pips": float(profile.spread_pips) if profile.spread_pips is not None else None,

        "slippage_model": profile.slippage_model,

        "slippage_per_side": str(entry.slippage),

        "slippage_pct": str(profile.slippage_pct),

        "pip_size": str(pip),

        "reference_price": str(reference_price),

        "entry_half_spread": str(entry.spread_cost),

        "exit_half_spread": str(exit_.spread_cost),

        "entry_cost_vs_mid": str(entry.fill_price - reference_price),

        "exit_cost_vs_mid": str(reference_price - exit_.fill_price),

        "round_trip_spread": str(rt_spread),

        "round_trip_slippage": str(rt_slip),

        "approx_round_trip_total": str(rt_spread + rt_slip),

        "round_trip_pips": (
            float(profile.spread_pips or 0) + float(profile.slippage_pips or 0) * 2
            if profile.slippage_model == "pips" and profile.spread_model == "pips"
            else None
        ),

    }





def describe_all_execution_costs() -> list[dict]:

    out: list[dict] = []

    for symbol, ref in AUDIT_REFERENCE_PRICES.items():

        out.append(describe_execution_cost(symbol, ref))

    return out


