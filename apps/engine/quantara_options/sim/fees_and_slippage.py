"""Honest Bid/Ask fill pricing, slippage, and fee calculation for simulated option orders.

Buys fill at the Ask, sells fill at the Bid. Wide/illiquid/stale quotes fill *worse*
than the natural side by a fraction of the half-spread. Mid is never used as a default
fill. Every USD amount scales by the leg's own contract `multiplier`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from quantara_options.contracts import AccountRuleProfile, LegPlan, OptionQuoteSnapshot

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def money(value: Any) -> Decimal:
    dec = to_decimal(value)
    if dec is None:
        return ZERO
    return dec.quantize(CENT, rounding=ROUND_HALF_UP)


def _round_to_tick(price: Decimal, tick: Decimal, rounding: str) -> Decimal:
    if tick <= ZERO:
        return price
    units = (price / tick).quantize(ONE, rounding=rounding)
    return units * tick


class FillAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class FillUnavailableError(ValueError):
    """Raised when no honest executable price exists for a leg."""


@dataclass(frozen=True)
class SlippageConfig:
    wide_spread_pct: Decimal = Decimal("0.10")        # (ask-bid)/mid above this => illiquid
    very_wide_spread_pct: Decimal = Decimal("0.25")   # doubles the slippage fraction
    min_volume_liquid: int = 10
    min_open_interest_liquid: int = 100
    combo_improvement_fraction: Decimal = Decimal("0")  # share of half-spread recaptured on liquid combos
    combo_liquid_spread_pct: Decimal = Decimal("0.05")
    estimated_spread_pct: Decimal = Decimal("0.10")   # synthetic spread around a model/mid-only price
    estimated_min_half_spread: Decimal = Decimal("0.05")
    option_tick: Decimal = Decimal("0.01")
    stock_half_spread_bps: Decimal = Decimal("2")     # 0.02% each side of spot for underlying fills


@dataclass(frozen=True)
class RegulatoryFeeSchedule:
    """Approximate pass-through regulatory fees charged on the sell side."""

    sec_fee_rate: Decimal = Decimal("0.0000278")           # $ per $ of sale proceeds
    finra_taf_per_option_contract: Decimal = Decimal("0.00279")
    finra_taf_per_share: Decimal = Decimal("0.000166")
    finra_taf_max_per_trade: Decimal = Decimal("8.30")
    orf_per_option_contract: Decimal = Decimal("0.02295")


DEFAULT_SLIPPAGE = SlippageConfig()
DEFAULT_REG_FEES = RegulatoryFeeSchedule()


@dataclass
class QuoteView:
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    mid: Optional[Decimal] = None
    last: Optional[Decimal] = None
    volume: int = 0
    open_interest: int = 0
    is_stale: bool = False
    source: str = "quote"

    @classmethod
    def from_snapshot(cls, snap: OptionQuoteSnapshot) -> "QuoteView":
        return cls(
            bid=to_decimal(snap.bid),
            ask=to_decimal(snap.ask),
            mid=to_decimal(snap.mid),
            last=to_decimal(snap.last_trade),
            volume=int(snap.volume or 0),
            open_interest=int(snap.open_interest or snap.contract.open_interest or 0),
            is_stale=bool(snap.is_stale),
            source="snapshot",
        )

    @classmethod
    def from_leg_plan(cls, leg: LegPlan) -> "QuoteView":
        return cls(
            bid=to_decimal(leg.bid),
            ask=to_decimal(leg.ask),
            mid=to_decimal(leg.mid),
            source="candidate",
        )

    def two_sided(self) -> bool:
        return (
            self.bid is not None
            and self.ask is not None
            and self.bid >= ZERO
            and self.ask > ZERO
            and self.ask >= self.bid
        )


@dataclass
class LegFill:
    action: FillAction
    quantity: int                 # contracts for options, shares for stock
    multiplier: int               # contract multiplier (1 for stock)
    price: Decimal                # executed price per share
    natural_price: Decimal        # ask for buys, bid for sells (before slippage/improvement)
    mid: Optional[Decimal]
    half_spread: Decimal
    spread_pct: Optional[Decimal]
    slippage_per_share: Decimal
    liquid: bool
    estimated: bool = False
    is_stock: bool = False
    occ_symbol: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def gross_usd(self) -> Decimal:
        return money(self.price * self.multiplier * self.quantity)

    @property
    def cash_flow_usd(self) -> Decimal:
        """+proceeds for sells, -cost for buys (before fees)."""
        return self.gross_usd if self.action == FillAction.SELL else -self.gross_usd

    @property
    def option_contracts(self) -> int:
        return 0 if self.is_stock else self.quantity


@dataclass
class MultiLegFill:
    legs: list[LegFill]
    combo_improvement_usd: Decimal = ZERO
    warnings: list[str] = field(default_factory=list)

    @property
    def net_cash_flow_usd(self) -> Decimal:
        return money(sum((l.cash_flow_usd for l in self.legs), ZERO))

    @property
    def option_net_cash_flow_usd(self) -> Decimal:
        return money(sum((l.cash_flow_usd for l in self.legs if not l.is_stock), ZERO))

    @property
    def estimated(self) -> bool:
        return any(l.estimated for l in self.legs)

    @property
    def mid_cash_flow_usd(self) -> Optional[Decimal]:
        total = ZERO
        for l in self.legs:
            if l.mid is None:
                return None
            gross = money(l.mid * l.multiplier * l.quantity)
            total += gross if l.action == FillAction.SELL else -gross
        return money(total)


def _synthetic_two_sided(center: Decimal, cfg: SlippageConfig) -> tuple[Decimal, Decimal]:
    half = max(cfg.estimated_min_half_spread, center * cfg.estimated_spread_pct / 2)
    return max(ZERO, center - half), center + half


def price_option_fill(
    action: FillAction,
    quote: QuoteView,
    *,
    quantity: int,
    multiplier: int,
    profile: AccountRuleProfile,
    config: SlippageConfig = DEFAULT_SLIPPAGE,
    fallback_price: Optional[Decimal] = None,
    allow_estimated: bool = False,
    occ_symbol: Optional[str] = None,
) -> LegFill:
    """Price one option leg at its natural side (Ask for buys, Bid for sells) plus slippage."""
    if quantity <= 0:
        raise FillUnavailableError("quantity must be positive")
    if multiplier <= 0:
        raise FillUnavailableError("multiplier must be positive")

    warnings: list[str] = []
    estimated = False
    if quote.two_sided():
        bid, ask = quote.bid, quote.ask  # type: ignore[assignment]
    elif allow_estimated:
        center = next(
            (p for p in (quote.mid, quote.last, fallback_price) if p is not None and p >= ZERO),
            None,
        )
        if center is None:
            raise FillUnavailableError(f"no executable quote or model price for {occ_symbol or 'leg'}")
        bid, ask = _synthetic_two_sided(center, config)
        if quote.bid is not None and quote.bid >= ZERO:
            bid = min(bid, quote.bid)
        if quote.ask is not None and quote.ask > ZERO:
            ask = max(ask, quote.ask)
        estimated = True
        warnings.append("estimated_fill_synthetic_spread")
    else:
        raise FillUnavailableError(f"missing two-sided bid/ask for {occ_symbol or 'leg'}")

    mid = (bid + ask) / 2
    half_spread = (ask - bid) / 2
    spread_pct = (ask - bid) / mid if mid > ZERO else None

    illiquid_reasons: list[str] = []
    if spread_pct is None or spread_pct > config.wide_spread_pct:
        illiquid_reasons.append("wide_spread")
    if (
        quote.source == "snapshot"
        and quote.volume < config.min_volume_liquid
        and quote.open_interest < config.min_open_interest_liquid
    ):
        illiquid_reasons.append("thin_volume_oi")
    if quote.is_stale:
        illiquid_reasons.append("stale_quote")
    if estimated:
        illiquid_reasons.append("estimated")

    slip_frac = ZERO
    if illiquid_reasons:
        slip_frac = to_decimal(profile.base_slippage_of_half_spread) or ZERO
        if spread_pct is None or spread_pct > config.very_wide_spread_pct or quote.is_stale:
            slip_frac *= 2
        warnings.extend(f"slippage:{r}" for r in illiquid_reasons)
    slippage = half_spread * slip_frac

    if action == FillAction.BUY:
        natural = ask
        price = _round_to_tick(ask + slippage, config.option_tick, ROUND_CEILING)
    else:
        natural = bid
        price = _round_to_tick(max(ZERO, bid - slippage), config.option_tick, ROUND_FLOOR)
        if bid <= ZERO:
            warnings.append("zero_bid")

    return LegFill(
        action=action,
        quantity=quantity,
        multiplier=multiplier,
        price=price,
        natural_price=natural,
        mid=mid,
        half_spread=half_spread,
        spread_pct=spread_pct,
        slippage_per_share=slippage,
        liquid=not illiquid_reasons,
        estimated=estimated,
        occ_symbol=occ_symbol,
        warnings=warnings,
    )


def price_stock_fill(
    action: FillAction,
    *,
    spot: Decimal,
    shares: int,
    config: SlippageConfig = DEFAULT_SLIPPAGE,
) -> LegFill:
    if shares <= 0 or spot is None or spot <= ZERO:
        raise FillUnavailableError("stock fill requires positive spot and shares")
    half = spot * config.stock_half_spread_bps / Decimal("10000")
    if action == FillAction.BUY:
        price = _round_to_tick(spot + half, CENT, ROUND_CEILING)
    else:
        price = _round_to_tick(spot - half, CENT, ROUND_FLOOR)
    return LegFill(
        action=action,
        quantity=shares,
        multiplier=1,
        price=price,
        natural_price=price,
        mid=spot,
        half_spread=half,
        spread_pct=None,
        slippage_per_share=ZERO,
        liquid=True,
        is_stock=True,
    )


def apply_combo_improvement(
    fills: Sequence[LegFill],
    config: SlippageConfig = DEFAULT_SLIPPAGE,
) -> MultiLegFill:
    """Optional bounded price improvement for liquid multi-leg option orders.

    Each option leg recaptures at most `combo_improvement_fraction` of its own half-spread,
    so the combo net can never be better than the combined mid.
    """
    legs = list(fills)
    option_legs = [l for l in legs if not l.is_stock]
    frac = config.combo_improvement_fraction
    result = MultiLegFill(legs=legs)
    if frac <= ZERO or len(option_legs) < 2:
        return result
    if not all(
        l.liquid and not l.estimated and l.spread_pct is not None and l.spread_pct <= config.combo_liquid_spread_pct
        for l in option_legs
    ):
        return result

    frac = min(frac, ONE)
    improvement = ZERO
    for l in option_legs:
        if l.mid is None:
            continue
        step = l.half_spread * frac
        if l.action == FillAction.BUY:
            new_px = max(l.mid, _round_to_tick(l.price - step, config.option_tick, ROUND_CEILING))
        else:
            new_px = min(l.mid, _round_to_tick(l.price + step, config.option_tick, ROUND_FLOOR))
        delta = abs(new_px - l.price) * l.multiplier * l.quantity
        improvement += delta
        l.price = new_px
        l.warnings.append("combo_improvement")
    result.combo_improvement_usd = money(improvement)
    return result


@dataclass
class FeeBreakdown:
    commission: Decimal = ZERO
    clearing: Decimal = ZERO
    regulatory: Decimal = ZERO
    assignment: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return money(self.commission + self.clearing + self.regulatory + self.assignment)

    def __add__(self, other: "FeeBreakdown") -> "FeeBreakdown":
        return FeeBreakdown(
            commission=self.commission + other.commission,
            clearing=self.clearing + other.clearing,
            regulatory=self.regulatory + other.regulatory,
            assignment=self.assignment + other.assignment,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "commission": float(money(self.commission)),
            "clearing": float(money(self.clearing)),
            "regulatory": float(money(self.regulatory)),
            "assignment": float(money(self.assignment)),
            "total": float(self.total),
        }


def _ceil_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_CEILING) if value > ZERO else ZERO


def sale_regulatory_fee(
    *,
    proceeds_usd: Decimal,
    option_contracts: int = 0,
    shares: int = 0,
    schedule: RegulatoryFeeSchedule = DEFAULT_REG_FEES,
) -> Decimal:
    """SEC Section 31 + FINRA TAF + ORF approximation for a sell execution."""
    sec = _ceil_cent(proceeds_usd * schedule.sec_fee_rate)
    taf_raw = (
        schedule.finra_taf_per_option_contract * option_contracts
        + schedule.finra_taf_per_share * shares
    )
    taf = _ceil_cent(min(taf_raw, schedule.finra_taf_max_per_trade))
    orf = _ceil_cent(schedule.orf_per_option_contract * option_contracts)
    return sec + taf + orf


def compute_fill_fees(
    fill: LegFill,
    profile: AccountRuleProfile,
    schedule: RegulatoryFeeSchedule = DEFAULT_REG_FEES,
) -> FeeBreakdown:
    fees = FeeBreakdown()
    if fill.is_stock:
        if fill.action == FillAction.SELL:
            fees.regulatory = sale_regulatory_fee(
                proceeds_usd=fill.gross_usd, shares=fill.quantity, schedule=schedule
            )
        return fees
    contracts = fill.quantity
    fees.commission = money(to_decimal(profile.commission_per_contract_usd) * contracts)
    fees.clearing = money(to_decimal(profile.clearing_fee_per_contract_usd) * contracts)
    if fill.action == FillAction.SELL:
        fees.regulatory = sale_regulatory_fee(
            proceeds_usd=fill.gross_usd, option_contracts=contracts, schedule=schedule
        )
    return fees


def compute_order_fees(
    fills: Iterable[LegFill],
    profile: AccountRuleProfile,
    schedule: RegulatoryFeeSchedule = DEFAULT_REG_FEES,
) -> FeeBreakdown:
    total = FeeBreakdown()
    for f in fills:
        total = total + compute_fill_fees(f, profile, schedule)
    return total


def compute_assignment_fees(
    profile: AccountRuleProfile,
    *,
    events: int = 1,
    stock_sale_proceeds_usd: Decimal = ZERO,
    shares_sold: int = 0,
    schedule: RegulatoryFeeSchedule = DEFAULT_REG_FEES,
) -> FeeBreakdown:
    """Fees for an assignment/exercise event (+ regulatory fees if shares are sold)."""
    fees = FeeBreakdown(assignment=money(to_decimal(profile.assignment_fee_usd) * events))
    if shares_sold > 0 and stock_sale_proceeds_usd > ZERO:
        fees.regulatory = sale_regulatory_fee(
            proceeds_usd=stock_sale_proceeds_usd, shares=shares_sold, schedule=schedule
        )
    return fees
