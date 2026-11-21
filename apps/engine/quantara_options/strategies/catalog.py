"""Strategy catalog for QUANTARA Options: metadata, entry/exit rules and open-position triggers.

Every number here is a deterministic rule used by the scanner, the Rules-mode advisor,
the risk guard and the close/roll evaluator. The AI advisor may only choose between
candidates built from these rules; it never changes them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from quantara_options.contracts import (
    ALL_9_STRATEGY_LABELS_HE,
    AIAction,
    LegRole,
    OptionChainResponse,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    StrategyType,
    WheelState,
)

# ---------------------------------------------------------------- rule primitives


@dataclass(frozen=True)
class DeltaBand:
    """Absolute-delta band (puts are matched on |delta|)."""

    min_abs: float
    max_abs: float

    @property
    def target(self) -> float:
        return round((self.min_abs + self.max_abs) / 2.0, 4)

    def contains(self, delta: Optional[float]) -> bool:
        if delta is None:
            return False
        return self.min_abs <= abs(delta) <= self.max_abs


@dataclass(frozen=True)
class LegRule:
    leg_role: LegRole
    side: OptionSide
    right: Optional[OptionRight]
    delta_band: Optional[DeltaBand]
    note_en: str
    note_he: str


@dataclass(frozen=True)
class ExitRules:
    take_profit_pct: float
    take_profit_basis: str  # "debit" | "credit" | "max_profit"
    stop_loss_pct: float
    stop_loss_basis: str  # "debit" | "credit_multiple" | "none"
    time_exit_dte: Optional[int] = None
    roll_dte: Optional[int] = None
    roll_short_delta_trigger: Optional[float] = None
    pin_risk_dte: Optional[int] = None
    roll_style: Optional[str] = None  # "up_and_out" | "down_and_out" | "out" | "untested_wing"
    accept_assignment: bool = False
    early_take_profit_pct: Optional[float] = None  # e.g. 50% credit early, 75% later


@dataclass(frozen=True)
class StrategySpec:
    strategy_type: StrategyType
    label_he: str
    label_en: str
    direction: str  # bullish | bearish | neutral | neutral_bullish | neutral_bearish
    iv_regime: str  # low_mid | mid_high | high | any
    is_credit: bool
    defined_risk: bool
    stock_backed: bool
    min_approval_level: int
    dte_min: int
    dte_max: int
    dte_target: int
    legs: tuple[LegRule, ...]
    exit: ExitRules
    max_profit_formula: str
    max_loss_formula: str
    collateral_formula: str
    break_even_formula: str
    entry_criteria_en: tuple[str, ...]
    entry_criteria_he: tuple[str, ...]
    risk_rules: tuple[str, ...]
    description_en: str
    description_he: str
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_type": self.strategy_type.value,
            "label_he": self.label_he,
            "label_en": self.label_en,
            "direction": self.direction,
            "iv_regime": self.iv_regime,
            "is_credit": self.is_credit,
            "defined_risk": self.defined_risk,
            "stock_backed": self.stock_backed,
            "min_approval_level": self.min_approval_level,
            "dte_window": [self.dte_min, self.dte_max],
            "dte_target": self.dte_target,
            "legs": [
                {
                    "leg_role": l.leg_role.value,
                    "side": l.side.value,
                    "right": l.right.value if l.right else None,
                    "delta_abs": [l.delta_band.min_abs, l.delta_band.max_abs] if l.delta_band else None,
                    "note_en": l.note_en,
                    "note_he": l.note_he,
                }
                for l in self.legs
            ],
            "exit": {
                "take_profit_pct": self.exit.take_profit_pct,
                "take_profit_basis": self.exit.take_profit_basis,
                "early_take_profit_pct": self.exit.early_take_profit_pct,
                "stop_loss_pct": self.exit.stop_loss_pct,
                "stop_loss_basis": self.exit.stop_loss_basis,
                "time_exit_dte": self.exit.time_exit_dte,
                "roll_dte": self.exit.roll_dte,
                "roll_short_delta_trigger": self.exit.roll_short_delta_trigger,
                "pin_risk_dte": self.exit.pin_risk_dte,
                "roll_style": self.exit.roll_style,
                "accept_assignment": self.exit.accept_assignment,
            },
            "max_profit_formula": self.max_profit_formula,
            "max_loss_formula": self.max_loss_formula,
            "collateral_formula": self.collateral_formula,
            "break_even_formula": self.break_even_formula,
            "entry_criteria_en": list(self.entry_criteria_en),
            "entry_criteria_he": list(self.entry_criteria_he),
            "risk_rules": list(self.risk_rules),
            "description_en": self.description_en,
            "description_he": self.description_he,
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


# ---------------------------------------------------------------- the catalog

_C, _P = OptionRight.CALL, OptionRight.PUT
_L, _S = OptionSide.LONG, OptionSide.SHORT

WING_WIDTH_MIN_USD = 2.0
WING_WIDTH_MAX_USD = 10.0

STRATEGY_CATALOG: dict[StrategyType, StrategySpec] = {
    StrategyType.LONG_CALL: StrategySpec(
        strategy_type=StrategyType.LONG_CALL,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.LONG_CALL.value],
        label_en="Long Call",
        direction="bullish",
        iv_regime="low_mid",
        is_credit=False,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=2,
        dte_min=14, dte_max=45, dte_target=30,
        legs=(LegRule(LegRole.LONG_CALL, _L, _C, DeltaBand(0.45, 0.65),
                      "Buy one call, delta 0.45-0.65", "קניית CALL בדלתא 0.45-0.65"),),
        exit=ExitRules(take_profit_pct=0.60, take_profit_basis="debit",
                       stop_loss_pct=0.35, stop_loss_basis="debit", time_exit_dte=5),
        max_profit_formula="unlimited",
        max_loss_formula="debit * multiplier * qty",
        collateral_formula="0 (debit paid in full)",
        break_even_formula="strike + debit",
        entry_criteria_en=("Bullish breakout / positive momentum", "IV low-to-mid (cheap premium)",
                           "Tight bid/ask, adequate OI"),
        entry_criteria_he=("פריצה שורית / מומנטום חיובי", "IV נמוכה-בינונית (פרמיה זולה)",
                           "מרווח Bid/Ask צר ו-OI מספק"),
        risk_rules=("Risk = full debit", "Close at +60% / -35% of debit", "Exit at <= 5 DTE (theta decay)"),
        description_en="Directional bullish bet with limited risk and unlimited upside.",
        description_he="הימור כיווני שורי עם סיכון מוגבל לפרמיה ורווח פוטנציאלי בלתי מוגבל.",
    ),
    StrategyType.LONG_PUT: StrategySpec(
        strategy_type=StrategyType.LONG_PUT,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.LONG_PUT.value],
        label_en="Long Put",
        direction="bearish",
        iv_regime="low_mid",
        is_credit=False,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=2,
        dte_min=14, dte_max=45, dte_target=30,
        legs=(LegRule(LegRole.LONG_PUT, _L, _P, DeltaBand(0.45, 0.65),
                      "Buy one put, delta -0.65 to -0.45", "קניית PUT בדלתא -0.65 עד -0.45"),),
        exit=ExitRules(take_profit_pct=0.60, take_profit_basis="debit",
                       stop_loss_pct=0.35, stop_loss_basis="debit", time_exit_dte=5),
        max_profit_formula="(strike - debit) * multiplier * qty",
        max_loss_formula="debit * multiplier * qty",
        collateral_formula="0 (debit paid in full)",
        break_even_formula="strike - debit",
        entry_criteria_en=("Bearish breakdown / negative momentum", "IV low-to-mid", "Tight bid/ask"),
        entry_criteria_he=("שבירה דובית / מומנטום שלילי", "IV נמוכה-בינונית", "מרווח Bid/Ask צר"),
        risk_rules=("Risk = full debit", "Close at +60% / -35% of debit", "Exit at <= 5 DTE"),
        description_en="Directional bearish bet / hedge with limited risk.",
        description_he="הימור כיווני דובי או גידור עם סיכון מוגבל לפרמיה.",
    ),
    StrategyType.COVERED_CALL: StrategySpec(
        strategy_type=StrategyType.COVERED_CALL,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.COVERED_CALL.value],
        label_en="Covered Call",
        direction="neutral_bullish",
        iv_regime="mid_high",
        is_credit=True,
        defined_risk=False,
        stock_backed=True,
        min_approval_level=1,
        dte_min=14, dte_max=45, dte_target=30,
        legs=(
            LegRule(LegRole.UNDERLYING_STOCK, _L, None, None,
                    "100 owned unencumbered shares per contract (or buy-write)",
                    "100 מניות פנויות לכל חוזה (או קנייה+כתיבה)"),
            LegRule(LegRole.SHORT_CALL, _S, _C, DeltaBand(0.20, 0.35),
                    "Sell OTM call, delta 0.20-0.35", "מכירת CALL מחוץ לכסף בדלתא 0.20-0.35"),
        ),
        exit=ExitRules(take_profit_pct=0.75, take_profit_basis="credit", early_take_profit_pct=0.50,
                       stop_loss_pct=0.0, stop_loss_basis="none", roll_dte=14,
                       roll_short_delta_trigger=0.55, roll_style="up_and_out", accept_assignment=True),
        max_profit_formula="(strike - share_basis + credit) * multiplier * qty",
        max_loss_formula="(share_basis - credit) * multiplier * qty (shares to zero)",
        collateral_formula="shares held (no cash collateral); buy-write pays share cost",
        break_even_formula="share_basis - credit",
        entry_criteria_en=("Own >= multiplier unencumbered shares, or buy-write is affordable",
                           "Neutral-to-mildly-bullish view", "Short strike above spot / cost basis"),
        entry_criteria_he=("החזקת מניות פנויות (או קנייה+כתיבה אם יש מזומן)",
                           "תחזית ניטרלית עד שורית מתונה", "סטרייק מעל מחיר המניה / בסיס העלות"),
        risk_rules=("Downside = stock risk minus credit", "Take 50% credit early, 75% late",
                    "Roll up & out if tested near 14 DTE"),
        description_en="Sell an OTM call against shares to harvest premium.",
        description_he="מכירת CALL מחוץ לכסף כנגד מניות מוחזקות לגביית פרמיה.",
    ),
    StrategyType.CASH_SECURED_PUT: StrategySpec(
        strategy_type=StrategyType.CASH_SECURED_PUT,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.CASH_SECURED_PUT.value],
        label_en="Cash-Secured Put",
        direction="neutral_bullish",
        iv_regime="mid_high",
        is_credit=True,
        defined_risk=False,
        stock_backed=True,
        min_approval_level=1,
        dte_min=21, dte_max=45, dte_target=35,
        legs=(LegRule(LegRole.SHORT_PUT, _S, _P, DeltaBand(0.18, 0.30),
                      "Sell OTM put, delta -0.30 to -0.18", "מכירת PUT מחוץ לכסף בדלתא -0.30 עד -0.18"),),
        exit=ExitRules(take_profit_pct=0.50, take_profit_basis="credit", stop_loss_pct=2.0,
                       stop_loss_basis="credit_multiple", roll_dte=14, roll_short_delta_trigger=0.50,
                       roll_style="down_and_out", accept_assignment=True),
        max_profit_formula="credit * multiplier * qty",
        max_loss_formula="(strike - credit) * multiplier * qty",
        collateral_formula="strike * multiplier * qty (cash reserved)",
        break_even_formula="strike - credit",
        entry_criteria_en=("Neutral/bullish on a stock you'd own", "IV mid-to-high", "Cash for full strike"),
        entry_criteria_he=("ניטרלי/שורי על מניה שמוכנים להחזיק", "IV בינונית-גבוהה", "מזומן מלא לסטרייק"),
        risk_rules=("Collateral = strike x 100", "Take 50% of credit",
                    "Roll down & out if ITM/tested, or accept assignment"),
        description_en="Sell an OTM put fully secured by cash; get paid to wait for a lower entry.",
        description_he="מכירת PUT מגובה במזומן מלא — מקבלים פרמיה תמורת המתנה לכניסה במחיר נמוך.",
    ),
    StrategyType.WHEEL: StrategySpec(
        strategy_type=StrategyType.WHEEL,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.WHEEL.value],
        label_en="Wheel",
        direction="neutral_bullish",
        iv_regime="mid_high",
        is_credit=True,
        defined_risk=False,
        stock_backed=True,
        min_approval_level=1,
        dte_min=21, dte_max=45, dte_target=35,
        legs=(
            LegRule(LegRole.SHORT_PUT, _S, _P, DeltaBand(0.18, 0.30),
                    "IDLE/SHORT_PUT: sell CSP delta -0.30 to -0.18",
                    "שלב PUT: מכירת CSP בדלתא -0.30 עד -0.18"),
            LegRule(LegRole.SHORT_CALL, _S, _C, DeltaBand(0.20, 0.35),
                    "HOLDING_SHARES: sell CC delta 0.20-0.35 above adjusted cost basis",
                    "שלב מניות: מכירת CALL בדלתא 0.20-0.35 מעל בסיס העלות המתואם"),
        ),
        exit=ExitRules(take_profit_pct=0.50, take_profit_basis="credit", stop_loss_pct=0.0,
                       stop_loss_basis="none", roll_dte=14, roll_short_delta_trigger=0.50,
                       roll_style="out", accept_assignment=True),
        max_profit_formula="put phase: credit; call phase: (call strike - adjusted basis + credit)",
        max_loss_formula="put phase: (strike - credit) * multiplier * qty",
        collateral_formula="put phase: strike * multiplier * qty; call phase: shares",
        break_even_formula="adjusted cost basis = assignment strike - all premiums collected",
        entry_criteria_en=("Quality underlying you accept owning", "IV mid-to-high",
                           "State machine IDLE -> SHORT_PUT -> HOLDING_SHARES -> SHORT_CALL -> IDLE"),
        entry_criteria_he=("נכס איכותי שמוכנים להחזיק", "IV בינונית-גבוהה",
                           "מכונת מצבים IDLE -> SHORT_PUT -> HOLDING_SHARES -> SHORT_CALL -> IDLE"),
        risk_rules=("Accept assignment by design", "Covered call strike >= adjusted cost basis",
                    "Take 50% credit, roll out when tested near 14 DTE"),
        description_en="Cycle CSPs until assigned, then covered calls above basis until called away.",
        description_he="מחזור CSP עד הקצאה, ואז Covered Call מעל בסיס העלות עד שהמניות נקראות.",
        extra={"states": [s.value for s in WheelState]},
    ),
    StrategyType.BULL_CALL_SPREAD: StrategySpec(
        strategy_type=StrategyType.BULL_CALL_SPREAD,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.BULL_CALL_SPREAD.value],
        label_en="Bull Call Spread",
        direction="bullish",
        iv_regime="any",
        is_credit=False,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=3,
        dte_min=14, dte_max=45, dte_target=30,
        legs=(
            LegRule(LegRole.LONG_CALL, _L, _C, DeltaBand(0.55, 0.65), "Buy call delta 0.55-0.65",
                    "קניית CALL בדלתא 0.55-0.65"),
            LegRule(LegRole.SHORT_CALL, _S, _C, DeltaBand(0.25, 0.35), "Sell higher call delta 0.25-0.35",
                    "מכירת CALL גבוה יותר בדלתא 0.25-0.35"),
        ),
        exit=ExitRules(take_profit_pct=0.60, take_profit_basis="max_profit", stop_loss_pct=0.45,
                       stop_loss_basis="debit", time_exit_dte=3, pin_risk_dte=5),
        max_profit_formula="(short strike - long strike - debit) * multiplier * qty",
        max_loss_formula="debit * multiplier * qty",
        collateral_formula="0 (debit paid in full)",
        break_even_formula="long strike + debit",
        entry_criteria_en=("Bullish, moderate target", "Debit <= 70% of width"),
        entry_criteria_he=("שורי עם יעד מתון", "דביט עד 70% מרוחב המרווח"),
        risk_rules=("Risk = debit", "TP 60% of max profit, SL 45% of debit", "Close before expiry pin risk"),
        description_en="Debit vertical: cheaper bullish exposure with capped profit.",
        description_he="ורטיקל דביט שורי — חשיפה זולה יותר עם רווח מוגבל.",
    ),
    StrategyType.BEAR_PUT_SPREAD: StrategySpec(
        strategy_type=StrategyType.BEAR_PUT_SPREAD,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.BEAR_PUT_SPREAD.value],
        label_en="Bear Put Spread",
        direction="bearish",
        iv_regime="any",
        is_credit=False,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=3,
        dte_min=14, dte_max=45, dte_target=30,
        legs=(
            LegRule(LegRole.LONG_PUT, _L, _P, DeltaBand(0.55, 0.65), "Buy put delta -0.65 to -0.55",
                    "קניית PUT בדלתא -0.65 עד -0.55"),
            LegRule(LegRole.SHORT_PUT, _S, _P, DeltaBand(0.25, 0.35), "Sell lower put delta -0.35 to -0.25",
                    "מכירת PUT נמוך יותר בדלתא -0.35 עד -0.25"),
        ),
        exit=ExitRules(take_profit_pct=0.60, take_profit_basis="max_profit", stop_loss_pct=0.45,
                       stop_loss_basis="debit", time_exit_dte=3, pin_risk_dte=5),
        max_profit_formula="(long strike - short strike - debit) * multiplier * qty",
        max_loss_formula="debit * multiplier * qty",
        collateral_formula="0 (debit paid in full)",
        break_even_formula="long strike - debit",
        entry_criteria_en=("Bearish, moderate target", "Debit <= 70% of width"),
        entry_criteria_he=("דובי עם יעד מתון", "דביט עד 70% מרוחב המרווח"),
        risk_rules=("Risk = debit", "TP 60% of max profit, SL 45% of debit", "Close before expiry pin risk"),
        description_en="Debit vertical: defined-risk bearish exposure.",
        description_he="ורטיקל דביט דובי עם סיכון מוגדר.",
    ),
    StrategyType.BULL_PUT_CREDIT_SPREAD: StrategySpec(
        strategy_type=StrategyType.BULL_PUT_CREDIT_SPREAD,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.BULL_PUT_CREDIT_SPREAD.value],
        label_en="Bull Put Credit Spread",
        direction="neutral_bullish",
        iv_regime="mid_high",
        is_credit=True,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=3,
        dte_min=21, dte_max=45, dte_target=35,
        legs=(
            LegRule(LegRole.SHORT_PUT, _S, _P, DeltaBand(0.16, 0.28), "Sell put delta -0.28 to -0.16",
                    "מכירת PUT בדלתא -0.28 עד -0.16"),
            LegRule(LegRole.LONG_PUT, _L, _P, None, "Buy lower put, wing $2-$10 by capital",
                    "קניית PUT נמוך יותר, כנף $2-$10 לפי ההון"),
        ),
        exit=ExitRules(take_profit_pct=0.50, take_profit_basis="credit", stop_loss_pct=2.0,
                       stop_loss_basis="credit_multiple", roll_dte=14, roll_short_delta_trigger=0.45,
                       pin_risk_dte=7, roll_style="down_and_out"),
        max_profit_formula="credit * multiplier * qty",
        max_loss_formula="(width - credit) * multiplier * qty",
        collateral_formula="(width - credit) * multiplier * qty",
        break_even_formula="short strike - credit",
        entry_criteria_en=("Neutral/bullish", "IV mid-to-high", "Credit >= 15% of width"),
        entry_criteria_he=("ניטרלי/שורי", "IV בינונית-גבוהה", "קרדיט לפחות 15% מרוחב הכנף"),
        risk_rules=("Defined risk = width - credit", "TP 50% credit, SL 2x credit (capped by max loss)",
                    "Roll/close at <= 14 DTE or short delta > 0.45"),
        description_en="Defined-risk bullish income spread below the market.",
        description_he="מרווח קרדיט שורי עם סיכון מוגדר מתחת למחיר השוק.",
    ),
    StrategyType.BEAR_CALL_CREDIT_SPREAD: StrategySpec(
        strategy_type=StrategyType.BEAR_CALL_CREDIT_SPREAD,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.BEAR_CALL_CREDIT_SPREAD.value],
        label_en="Bear Call Credit Spread",
        direction="neutral_bearish",
        iv_regime="mid_high",
        is_credit=True,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=3,
        dte_min=21, dte_max=45, dte_target=35,
        legs=(
            LegRule(LegRole.SHORT_CALL, _S, _C, DeltaBand(0.16, 0.28), "Sell call delta 0.16-0.28",
                    "מכירת CALL בדלתא 0.16-0.28"),
            LegRule(LegRole.LONG_CALL, _L, _C, None, "Buy higher call, wing $2-$10 by capital",
                    "קניית CALL גבוה יותר, כנף $2-$10 לפי ההון"),
        ),
        exit=ExitRules(take_profit_pct=0.50, take_profit_basis="credit", stop_loss_pct=2.0,
                       stop_loss_basis="credit_multiple", roll_dte=14, roll_short_delta_trigger=0.45,
                       pin_risk_dte=7, roll_style="up_and_out"),
        max_profit_formula="credit * multiplier * qty",
        max_loss_formula="(width - credit) * multiplier * qty",
        collateral_formula="(width - credit) * multiplier * qty",
        break_even_formula="short strike + credit",
        entry_criteria_en=("Neutral/bearish", "IV mid-to-high", "Credit >= 15% of width"),
        entry_criteria_he=("ניטרלי/דובי", "IV בינונית-גבוהה", "קרדיט לפחות 15% מרוחב הכנף"),
        risk_rules=("Defined risk = width - credit", "TP 50% credit, SL 2x credit (capped by max loss)",
                    "Roll/close at <= 14 DTE or short delta > 0.45"),
        description_en="Defined-risk bearish income spread above the market.",
        description_he="מרווח קרדיט דובי עם סיכון מוגדר מעל מחיר השוק.",
    ),
    StrategyType.IRON_CONDOR: StrategySpec(
        strategy_type=StrategyType.IRON_CONDOR,
        label_he=ALL_9_STRATEGY_LABELS_HE[StrategyType.IRON_CONDOR.value],
        label_en="Iron Condor",
        direction="neutral",
        iv_regime="high",
        is_credit=True,
        defined_risk=True,
        stock_backed=False,
        min_approval_level=3,
        dte_min=21, dte_max=45, dte_target=35,
        legs=(
            LegRule(LegRole.SHORT_PUT, _S, _P, DeltaBand(0.15, 0.25), "Sell put delta ~-0.20",
                    "מכירת PUT בדלתא ~-0.20"),
            LegRule(LegRole.LONG_PUT, _L, _P, None, "Buy lower put wing", "קניית כנף PUT נמוכה"),
            LegRule(LegRole.SHORT_CALL, _S, _C, DeltaBand(0.15, 0.25), "Sell call delta ~0.20",
                    "מכירת CALL בדלתא ~0.20"),
            LegRule(LegRole.LONG_CALL, _L, _C, None, "Buy higher call wing (symmetric)",
                    "קניית כנף CALL גבוהה (סימטרית)"),
        ),
        exit=ExitRules(take_profit_pct=0.50, take_profit_basis="credit", stop_loss_pct=2.0,
                       stop_loss_basis="credit_multiple", roll_dte=10, roll_short_delta_trigger=0.35,
                       pin_risk_dte=7, roll_style="untested_wing"),
        max_profit_formula="total credit * multiplier * qty",
        max_loss_formula="(wider wing width - credit) * multiplier * qty",
        collateral_formula="(wing width - credit) * multiplier * qty",
        break_even_formula="short put - credit / short call + credit",
        entry_criteria_en=("Range-bound view", "High IV", "Symmetric wings, credit >= 15% of width"),
        entry_criteria_he=("ציפייה לטווח מסחר", "IV גבוהה", "כנפיים סימטריות, קרדיט לפחות 15% מהרוחב"),
        risk_rules=("Defined risk = wing - credit", "TP 50% credit, SL 2x credit",
                    "Roll untested wing when one side is tested; close if a short strike is breached"),
        description_en="Neutral defined-risk income: short strangle with protective wings.",
        description_he="הכנסה ניטרלית עם סיכון מוגדר: סטריינגל שורט עם כנפי הגנה.",
    ),
}

DIRECTIONAL_DEBIT = {StrategyType.LONG_CALL, StrategyType.LONG_PUT}
DEBIT_VERTICALS = {StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_PUT_SPREAD}
CREDIT_VERTICALS = {StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD}
STOCK_BACKED = {StrategyType.COVERED_CALL, StrategyType.CASH_SECURED_PUT, StrategyType.WHEEL}


def get_strategy_spec(strategy_type: StrategyType | str) -> StrategySpec:
    return STRATEGY_CATALOG[StrategyType(strategy_type)]


def catalog_as_dicts() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in STRATEGY_CATALOG.values()]


def strategies_for_approval_level(level: int) -> list[StrategyType]:
    return [t for t, s in STRATEGY_CATALOG.items() if s.min_approval_level <= level]


SCOPE_ALIASES: dict[str, tuple[StrategyType, ...]] = {
    "long_only": (StrategyType.LONG_CALL, StrategyType.LONG_PUT),
    "credit_spreads": (StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD),
    "credit_verticals": (StrategyType.BULL_PUT_CREDIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD),
    "debit_spreads": (StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_PUT_SPREAD),
    "wheel_csp_cc": (StrategyType.CASH_SECURED_PUT, StrategyType.COVERED_CALL, StrategyType.WHEEL),
}


def parse_strategy_scope(scope: Optional[str]) -> list[StrategyType]:
    """None/"all"/"multi" = all strategies; otherwise comma-separated strategy_type slugs or
    cohort aliases (`long_only`, `credit_spreads`, `wheel_csp_cc`, `iron_condor`, ...)."""
    if not scope or str(scope).strip().lower() in {"all", "multi", "multi_strategy", "*"}:
        return list(STRATEGY_CATALOG.keys())
    out: list[StrategyType] = []
    for part in str(scope).split(","):
        part = part.strip().lower()
        if part in SCOPE_ALIASES:
            out.extend(t for t in SCOPE_ALIASES[part] if t not in out)
            continue
        try:
            st = StrategyType(part)
        except ValueError:
            continue
        if st not in out:
            out.append(st)
    return out or list(STRATEGY_CATALOG.keys())


def wing_width_for_capital(equity: float | Decimal | None, spot: float | Decimal | None = None) -> float:
    """Target credit-spread / condor wing width ($2-$10) scaled to account capital and spot."""
    eq = float(equity or 10_000)
    if eq < 7_500:
        width = 2.0
    elif eq < 15_000:
        width = 3.0 if (spot is not None and float(spot) < 150) else 5.0
    elif eq < 40_000:
        width = 5.0
    else:
        width = 10.0
    if spot is not None and float(spot) > 400 and eq >= 15_000:
        width = max(width, 5.0)
    return float(min(WING_WIDTH_MAX_USD, max(WING_WIDTH_MIN_USD, width)))


# ---------------------------------------------------------------- wheel state machine

WHEEL_TRANSITIONS: dict[tuple[WheelState, str], WheelState] = {
    (WheelState.IDLE, "open_put"): WheelState.SHORT_PUT,
    (WheelState.SHORT_PUT, "put_closed"): WheelState.IDLE,
    (WheelState.SHORT_PUT, "put_expired"): WheelState.IDLE,
    (WheelState.SHORT_PUT, "put_assigned"): WheelState.HOLDING_SHARES,
    (WheelState.HOLDING_SHARES, "open_call"): WheelState.SHORT_CALL,
    (WheelState.SHORT_CALL, "call_closed"): WheelState.HOLDING_SHARES,
    (WheelState.SHORT_CALL, "call_expired"): WheelState.HOLDING_SHARES,
    (WheelState.SHORT_CALL, "called_away"): WheelState.IDLE,
    (WheelState.HOLDING_SHARES, "shares_sold"): WheelState.IDLE,
}


def next_wheel_state(current: WheelState | str | None, event: str) -> WheelState:
    state = WheelState(current) if current else WheelState.IDLE
    return WHEEL_TRANSITIONS.get((state, event), state)


def derive_wheel_state(
    *,
    has_open_short_put: bool,
    has_open_short_call: bool,
    shares_held: int,
    multiplier: int = 100,
) -> WheelState:
    if has_open_short_call and shares_held >= multiplier:
        return WheelState.SHORT_CALL
    if has_open_short_put:
        return WheelState.SHORT_PUT
    if shares_held >= multiplier:
        return WheelState.HOLDING_SHARES
    return WheelState.IDLE


def wheel_leg_for_state(state: WheelState | str | None) -> Optional[LegRole]:
    """Which short leg the wheel may open in this state (None = nothing to open)."""
    st = WheelState(state) if state else WheelState.IDLE
    if st == WheelState.IDLE:
        return LegRole.SHORT_PUT
    if st == WheelState.HOLDING_SHARES:
        return LegRole.SHORT_CALL
    return None


def adjusted_cost_basis(assignment_strike: float, premiums_per_share: Iterable[float]) -> float:
    return round(float(assignment_strike) - sum(float(p) for p in premiums_per_share), 4)


# ---------------------------------------------------------------- open-position evaluation


@dataclass
class StrategyActionRecommendation:
    position_group_id: Optional[str]
    strategy_type: StrategyType
    underlying_symbol: str
    action: AIAction
    reason_code: str
    rationale_he: str
    rationale_en: str
    urgency: str = "normal"  # low | normal | high
    mandatory: bool = False  # risk-authority exits the AI may not override with HOLD
    metrics: dict[str, Any] = field(default_factory=dict)
    roll_hint: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_group_id": self.position_group_id,
            "strategy_type": self.strategy_type.value,
            "underlying_symbol": self.underlying_symbol,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "rationale_he": self.rationale_he,
            "rationale_en": self.rationale_en,
            "urgency": self.urgency,
            "mandatory": self.mandatory,
            "metrics": self.metrics,
            "roll_hint": self.roll_hint,
        }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def _enum_val(value: Any) -> Any:
    return getattr(value, "value", value)


def quote_index(chain_or_quotes: Any) -> tuple[dict[str, OptionQuoteSnapshot], dict[str, float]]:
    """Normalize an OptionChainResponse / {occ: quote} / [quotes] / [chains] into
    (quotes by OCC, underlying spot by symbol)."""
    index: dict[str, OptionQuoteSnapshot] = {}
    spots: dict[str, float] = {}
    if chain_or_quotes is None:
        return index, spots
    items: list[Any]
    if isinstance(chain_or_quotes, OptionChainResponse):
        items = [chain_or_quotes]
    elif isinstance(chain_or_quotes, Mapping):
        items = list(chain_or_quotes.values())
    elif isinstance(chain_or_quotes, (list, tuple)):
        items = list(chain_or_quotes)
    else:
        items = [chain_or_quotes]
    for item in items:
        if isinstance(item, OptionChainResponse):
            px = _f(item.underlying_price)
            if px:
                spots[item.underlying_symbol.upper()] = px
            for q in item.contracts:
                index[q.contract.occ_symbol] = q
        elif isinstance(item, OptionQuoteSnapshot):
            index[item.contract.occ_symbol] = item
            sym = item.contract.underlying_symbol.upper()
            px = _f(item.underlying_price)
            if px and sym not in spots:
                spots[sym] = px
    return index, spots


def _quote_mid(q: OptionQuoteSnapshot) -> Optional[float]:
    mid = _f(q.mid)
    if mid is not None and mid > 0:
        return mid
    bid, ask = _f(q.bid), _f(q.ask)
    if bid is not None and ask is not None and ask >= bid and ask > 0:
        return (bid + ask) / 2.0
    return _f(q.last_trade)


def _dte_of(view: Any, now: datetime) -> Optional[int]:
    exp = _get(view, "expiration_date")
    if isinstance(exp, str):
        try:
            exp = date.fromisoformat(exp[:10])
        except ValueError:
            exp = None
    if isinstance(exp, datetime):
        exp = exp.date()
    if isinstance(exp, date):
        return (exp - now.date()).days
    dte = _get(view, "dte")
    return int(dte) if dte is not None else None


def _rec(view: Any, st: StrategyType, action: AIAction, code: str, he: str, en: str, *,
         urgency: str = "normal", mandatory: bool = False, metrics: dict[str, Any],
         roll_hint: Optional[dict[str, Any]] = None) -> StrategyActionRecommendation:
    return StrategyActionRecommendation(
        position_group_id=_get(view, "id") or _get(view, "position_group_id"),
        strategy_type=st,
        underlying_symbol=str(_get(view, "underlying_symbol", "") or ""),
        action=action,
        reason_code=code,
        rationale_he=he,
        rationale_en=en,
        urgency=urgency,
        mandatory=mandatory,
        metrics=metrics,
        roll_hint=roll_hint,
    )


def evaluate_open_strategy_actions(
    strategy_view: Any,
    chain_or_quotes: Any = None,
    now: Optional[datetime] = None,
    *,
    spot: Optional[float] = None,
) -> StrategyActionRecommendation:
    """Deterministic HOLD / CLOSE / ROLL recommendation for one open strategy group.

    `strategy_view` is an `OptionsStrategyGroupView` (or equivalent dict). Leg marks are
    refreshed from `chain_or_quotes` when available, otherwise the view's `current_mark` is used.
    The underlying spot is taken from `spot`, else from quotes of THIS underlying only.
    P&L is computed on OPTION legs only (stock legs are judged by spot vs. strikes).
    """
    now = now or datetime.now(timezone.utc)
    st = StrategyType(_enum_val(_get(strategy_view, "strategy_type")))
    spec = STRATEGY_CATALOG[st]
    index, spots = quote_index(chain_or_quotes)
    underlying = str(_get(strategy_view, "underlying_symbol", "") or "").upper()

    legs = list(_get(strategy_view, "legs", []) or [])
    opt_legs = []
    spot = _f(spot) or spots.get(underlying)
    for leg in legs:
        if str(_enum_val(_get(leg, "status", "open")) or "open").lower() not in {"open", ""}:
            continue
        occ = _get(leg, "occ_symbol")
        if not occ:
            continue
        side = str(_enum_val(_get(leg, "side", "long"))).lower()
        sign = 1.0 if side == "long" else -1.0
        mult = int(_get(leg, "multiplier", 100) or 100)
        qty = int(_get(leg, "quantity", 1) or 1)
        entry = _f(_get(leg, "entry_price"), 0.0) or 0.0
        mark = _f(_get(leg, "current_mark"))
        delta = _f(_get(leg, "delta"))
        q = index.get(occ)
        if q is not None:
            qm = _quote_mid(q)
            if qm is not None:
                mark = qm
            if q.greeks and q.greeks.delta is not None:
                delta = float(q.greeks.delta)
            if spot is None:
                spot = _f(q.underlying_price)
        if mark is None:
            mark = entry
        opt_legs.append({
            "occ": occ, "sign": sign, "mult": mult, "qty": qty, "entry": entry, "mark": mark,
            "delta": delta, "right": str(_enum_val(_get(leg, "right", "")) or "").lower(),
            "strike": _f(_get(leg, "strike")), "role": str(_enum_val(_get(leg, "leg_role", "")) or ""),
        })

    dte = _dte_of(strategy_view, now)
    metrics: dict[str, Any] = {"dte": dte, "spot": spot}
    if not opt_legs:
        return _rec(strategy_view, st, AIAction.HOLD, "no_option_legs",
                    "אין רגלי אופציה פתוחות — אין פעולה נדרשת.",
                    "No open option legs — nothing to manage.", urgency="low", metrics=metrics)

    entry_value = sum(l["sign"] * l["entry"] * l["mult"] * l["qty"] for l in opt_legs)
    current_value = sum(l["sign"] * l["mark"] * l["mult"] * l["qty"] for l in opt_legs)
    pnl = current_value - entry_value
    is_credit_pos = entry_value < 0
    basis = abs(entry_value) if abs(entry_value) > 1e-9 else None
    pnl_pct = (pnl / basis) if basis else None
    shorts = [l for l in opt_legs if l["sign"] < 0]
    max_short_delta = max((abs(l["delta"]) for l in shorts if l["delta"] is not None), default=None)
    max_profit = _f(_get(strategy_view, "max_profit_usd"))
    max_loss = _f(_get(strategy_view, "max_loss_usd"))
    metrics.update({
        "option_pnl_usd": round(pnl, 2),
        "pnl_pct_of_basis": round(pnl_pct, 4) if pnl_pct is not None else None,
        "entry_basis_usd": round(basis or 0.0, 2),
        "is_credit": is_credit_pos,
        "max_short_delta": max_short_delta,
    })
    ex = spec.exit
    pct_txt = f"{(pnl_pct or 0) * 100:+.1f}%"

    def tested(leg: dict[str, Any], band: float = 0.0) -> bool:
        if spot is None or leg["strike"] is None:
            return False
        if leg["right"] == "call":
            return spot >= leg["strike"] * (1.0 - band)
        return spot <= leg["strike"] * (1.0 + band)

    # -------- long single options
    if st in DIRECTIONAL_DEBIT:
        if pnl_pct is not None and pnl_pct >= ex.take_profit_pct:
            return _rec(strategy_view, st, AIAction.CLOSE, "take_profit",
                        f"יעד רווח הושג ({pct_txt} מהפרמיה ששולמה, יעד +{ex.take_profit_pct:.0%}) — סגירה.",
                        f"Take-profit hit ({pct_txt} of debit, target +{ex.take_profit_pct:.0%}) — close.",
                        metrics=metrics)
        if pnl_pct is not None and pnl_pct <= -ex.stop_loss_pct:
            return _rec(strategy_view, st, AIAction.CLOSE, "stop_loss",
                        f"סטופ לוס ({pct_txt} מהפרמיה, גבול -{ex.stop_loss_pct:.0%}) — סגירה מחייבת.",
                        f"Stop-loss ({pct_txt} of debit, limit -{ex.stop_loss_pct:.0%}) — mandatory close.",
                        urgency="high", mandatory=True, metrics=metrics)
        if dte is not None and ex.time_exit_dte is not None and dte <= ex.time_exit_dte:
            return _rec(strategy_view, st, AIAction.CLOSE, "time_exit",
                        f"נותרו {dte} ימים לפקיעה (≤{ex.time_exit_dte}) — יציאת זמן לפני שחיקת תטא.",
                        f"{dte} DTE left (<= {ex.time_exit_dte}) — time exit before theta decay.",
                        urgency="high", mandatory=True, metrics=metrics)
        return _rec(strategy_view, st, AIAction.HOLD, "within_plan",
                    f"בתוך התוכנית: רווח/הפסד {pct_txt}, {dte} ימים לפקיעה.",
                    f"Within plan: P&L {pct_txt}, {dte} DTE.", urgency="low", metrics=metrics)

    # -------- debit verticals
    if st in DEBIT_VERTICALS:
        mp = max_profit if max_profit and max_profit > 0 else None
        pct_mp = (pnl / mp) if mp else None
        metrics["pnl_pct_of_max_profit"] = round(pct_mp, 4) if pct_mp is not None else None
        if pct_mp is not None and pct_mp >= ex.take_profit_pct:
            return _rec(strategy_view, st, AIAction.CLOSE, "take_profit",
                        f"הושגו {pct_mp:.0%} מהרווח המקסימלי (יעד {ex.take_profit_pct:.0%}) — סגירה.",
                        f"Captured {pct_mp:.0%} of max profit (target {ex.take_profit_pct:.0%}) — close.",
                        metrics=metrics)
        if pnl_pct is not None and pnl_pct <= -ex.stop_loss_pct:
            return _rec(strategy_view, st, AIAction.CLOSE, "stop_loss",
                        f"הפסד {pct_txt} מהדביט (גבול -{ex.stop_loss_pct:.0%}) — סגירה מחייבת.",
                        f"Loss {pct_txt} of debit (limit -{ex.stop_loss_pct:.0%}) — mandatory close.",
                        urgency="high", mandatory=True, metrics=metrics)
        if dte is not None and ex.time_exit_dte is not None and dte <= ex.time_exit_dte:
            return _rec(strategy_view, st, AIAction.CLOSE, "pin_risk_expiry",
                        f"{dte} ימים לפקיעה — סגירה לפני סיכון Pin/הקצאה.",
                        f"{dte} DTE — close before expiry pin/assignment risk.",
                        urgency="high", mandatory=True, metrics=metrics)
        if dte is not None and ex.pin_risk_dte is not None and dte <= ex.pin_risk_dte and spot is not None:
            near = [l for l in shorts if l["strike"] and abs(spot - l["strike"]) / l["strike"] <= 0.01]
            if near:
                return _rec(strategy_view, st, AIAction.CLOSE, "pin_risk",
                            f"המחיר צמוד לסטרייק השורט ({near[0]['strike']}) עם {dte} ימים — סיכון Pin, סגירה.",
                            f"Spot pinned at short strike {near[0]['strike']} with {dte} DTE — close.",
                            urgency="high", mandatory=True, metrics=metrics)
        return _rec(strategy_view, st, AIAction.HOLD, "within_plan",
                    f"בתוך התוכנית: {pct_txt} מהדביט, {dte} ימים לפקיעה.",
                    f"Within plan: {pct_txt} of debit, {dte} DTE.", urgency="low", metrics=metrics)

    # -------- credit structures (CC, CSP, wheel, credit verticals, condor)
    credit = basis if is_credit_pos else None
    capture = (pnl / credit) if credit else None
    metrics["credit_captured_pct"] = round(capture, 4) if capture is not None else None
    cap_txt = f"{(capture or 0) * 100:.0f}%"
    loss_usd = -pnl if pnl < 0 else 0.0

    is_wheel = st == StrategyType.WHEEL
    short_call = next((l for l in shorts if l["right"] == "call"), None)
    short_put = next((l for l in shorts if l["right"] == "put"), None)
    if is_wheel:
        metrics["wheel_phase"] = "short_call" if short_call else "short_put"

    # Take profit (early 50% then 75% for covered calls)
    tp = ex.take_profit_pct
    if ex.early_take_profit_pct is not None and dte is not None and dte > (ex.roll_dte or 14):
        tp = ex.early_take_profit_pct
    if capture is not None and capture >= tp:
        return _rec(strategy_view, st, AIAction.CLOSE, "take_profit",
                    f"נגבו {cap_txt} מהקרדיט (יעד {tp:.0%}) — קניה חזרה ושחרור בטוחה.",
                    f"Captured {cap_txt} of credit (target {tp:.0%}) — buy back and free collateral.",
                    metrics=metrics)

    # Stop loss: multiple of credit, capped by max loss
    if ex.stop_loss_basis == "credit_multiple" and credit:
        sl_usd = ex.stop_loss_pct * credit
        if max_loss and max_loss > 0:
            sl_usd = min(sl_usd, 0.9 * max_loss)
        metrics["stop_loss_usd"] = round(sl_usd, 2)
        if loss_usd >= sl_usd and st != StrategyType.CASH_SECURED_PUT:
            return _rec(strategy_view, st, AIAction.CLOSE, "stop_loss",
                        f"הפסד ${loss_usd:,.0f} ≥ {ex.stop_loss_pct:.0f}× קרדיט (${sl_usd:,.0f}) — סגירה מחייבת.",
                        f"Loss ${loss_usd:,.0f} >= {ex.stop_loss_pct:.0f}x credit (${sl_usd:,.0f}) — mandatory close.",
                        urgency="high", mandatory=True, metrics=metrics)
        if loss_usd >= sl_usd:
            return _rec(strategy_view, st, AIAction.ROLL, "loss_roll",
                        f"הפסד ${loss_usd:,.0f} ≥ {ex.stop_loss_pct:.0f}× קרדיט — גלגול למטה והחוצה לקבלת קרדיט נוסף.",
                        f"Loss ${loss_usd:,.0f} >= {ex.stop_loss_pct:.0f}x credit — roll down & out for credit.",
                        urgency="high", metrics=metrics,
                        roll_hint={"style": "down_and_out", "min_dte": spec.dte_min, "max_dte": spec.dte_max,
                                   "target_delta": 0.25, "require_net_credit": True})

    # Iron condor: breach / tested side
    if st == StrategyType.IRON_CONDOR:
        breached = [l for l in shorts if tested(l)]
        if breached:
            side_he = "CALL" if breached[0]["right"] == "call" else "PUT"
            return _rec(strategy_view, st, AIAction.CLOSE, "short_strike_breached",
                        f"המחיר חצה את סטרייק ה-{side_he} השורט ({breached[0]['strike']}) — סגירת הקונדור.",
                        f"Spot breached the short {side_he} strike {breached[0]['strike']} — close the condor.",
                        urgency="high", mandatory=True, metrics=metrics)
        if dte is not None and ex.pin_risk_dte is not None and dte <= ex.pin_risk_dte:
            return _rec(strategy_view, st, AIAction.CLOSE, "gamma_risk_expiry",
                        f"{dte} ימים לפקיעה — סגירה לפני סיכון גאמא.",
                        f"{dte} DTE — close before gamma risk.", urgency="high", mandatory=True,
                        metrics=metrics)
        tested_legs = [l for l in shorts if l["delta"] is not None
                       and abs(l["delta"]) >= (ex.roll_short_delta_trigger or 0.35)]
        if tested_legs:
            t = tested_legs[0]
            untested = "put" if t["right"] == "call" else "call"
            if dte is not None and dte > (ex.roll_dte or 10):
                return _rec(strategy_view, st, AIAction.ROLL, "tested_side_roll_untested",
                            f"צד ה-{t['right'].upper()} נבחן (דלתא {abs(t['delta']):.2f}) — גלגול הכנף הלא-נבחנת "
                            f"({untested.upper()}) קרוב יותר לגביית קרדיט נוסף.",
                            f"{t['right'].upper()} side tested (delta {abs(t['delta']):.2f}) — roll the untested "
                            f"{untested.upper()} wing closer for extra credit.",
                            urgency="normal", metrics=metrics,
                            roll_hint={"style": "untested_wing", "untested_right": untested,
                                       "target_delta": 0.25, "keep_expiration": True,
                                       "require_net_credit": True})
            return _rec(strategy_view, st, AIAction.CLOSE, "tested_late",
                        f"צד נבחן עם {dte} ימים בלבד — סגירה.",
                        f"Side tested with only {dte} DTE — close.", urgency="high", metrics=metrics)
        return _rec(strategy_view, st, AIAction.HOLD, "within_range",
                    f"בטווח: נגבו {cap_txt} מהקרדיט, {dte} ימים לפקיעה.",
                    f"In range: {cap_txt} of credit captured, {dte} DTE.", urgency="low", metrics=metrics)

    # Credit verticals: delta trigger / DTE management
    if st in CREDIT_VERTICALS:
        s = shorts[0] if shorts else None
        delta_hit = (s is not None and s["delta"] is not None
                     and abs(s["delta"]) > (ex.roll_short_delta_trigger or 0.45))
        style = ex.roll_style or "out"
        if dte is not None and ex.pin_risk_dte is not None and dte <= ex.pin_risk_dte and (delta_hit or pnl < 0):
            return _rec(strategy_view, st, AIAction.CLOSE, "gamma_risk_expiry",
                        f"{dte} ימים לפקיעה והפוזיציה בלחץ — סגירה (ללא גלגול בגאמא גבוהה).",
                        f"{dte} DTE and position under pressure — close (no rolling into high gamma).",
                        urgency="high", mandatory=True, metrics=metrics)
        if delta_hit:
            return _rec(strategy_view, st, AIAction.ROLL, "short_delta_trigger",
                        f"דלתת הרגל השורטית {abs(s['delta']):.2f} > {ex.roll_short_delta_trigger} — גלגול "
                        f"{'למטה' if style == 'down_and_out' else 'למעלה'} והחוצה.",
                        f"Short-leg delta {abs(s['delta']):.2f} > {ex.roll_short_delta_trigger} — roll "
                        f"{style.replace('_', ' ')}.",
                        urgency="high", metrics=metrics,
                        roll_hint={"style": style, "min_dte": spec.dte_min, "max_dte": spec.dte_max,
                                   "target_delta": 0.22, "require_net_credit": True})
        if dte is not None and ex.roll_dte is not None and dte <= ex.roll_dte:
            if pnl >= 0:
                return _rec(strategy_view, st, AIAction.CLOSE, "time_exit_profitable",
                            f"{dte} ימים לפקיעה ברווח ({cap_txt} מהקרדיט) — סגירה ונעילת רווח.",
                            f"{dte} DTE in profit ({cap_txt} of credit) — close and lock gains.",
                            metrics=metrics)
            return _rec(strategy_view, st, AIAction.ROLL, "time_roll_losing",
                        f"{dte} ימים לפקיעה בהפסד — גלגול החוצה לקרדיט נוסף.",
                        f"{dte} DTE at a loss — roll out for additional credit.", urgency="normal",
                        metrics=metrics,
                        roll_hint={"style": style, "min_dte": spec.dte_min, "max_dte": spec.dte_max,
                                   "target_delta": 0.22, "require_net_credit": True})
        return _rec(strategy_view, st, AIAction.HOLD, "within_plan",
                    f"בתוך התוכנית: נגבו {cap_txt} מהקרדיט, {dte} ימים לפקיעה.",
                    f"Within plan: {cap_txt} of credit captured, {dte} DTE.", urgency="low", metrics=metrics)

    # Covered call / CSP / wheel
    roll_dte = ex.roll_dte or 14
    if short_call is not None:
        call_tested = tested(short_call, band=0.01) or (
            short_call["delta"] is not None and abs(short_call["delta"]) >= (ex.roll_short_delta_trigger or 0.55))
        if call_tested and dte is not None and dte <= roll_dte:
            if is_wheel or dte <= 1:
                return _rec(strategy_view, st, AIAction.HOLD, "accept_call_away",
                            f"ה-CALL בכסף ({dte} ימים) — מאפשרים למניות להיקרא (הגלגל חוזר ל-IDLE ברווח).",
                            f"Short call ITM ({dte} DTE) — let shares be called away (wheel returns to IDLE).",
                            urgency="normal", metrics=metrics)
            return _rec(strategy_view, st, AIAction.ROLL, "call_tested_roll_up_out",
                        f"ה-CALL השורט נבחן עם {dte} ימים — גלגול למעלה והחוצה לשמירת המניות.",
                        f"Short call tested at {dte} DTE — roll up & out to keep the shares.",
                        urgency="normal", metrics=metrics,
                        roll_hint={"style": "up_and_out", "min_dte": 14, "max_dte": 45,
                                   "target_delta": 0.30, "min_strike": short_call["strike"],
                                   "require_net_credit": True})
    if short_put is not None:
        put_tested = tested(short_put) or (
            short_put["delta"] is not None and abs(short_put["delta"]) >= (ex.roll_short_delta_trigger or 0.50))
        if put_tested and dte is not None and dte <= roll_dte:
            if is_wheel:
                return _rec(strategy_view, st, AIAction.HOLD, "accept_assignment",
                            f"ה-PUT בכסף ({dte} ימים) — מקבלים הקצאה לפי תוכנית הגלגל (מעבר ל-HOLDING_SHARES).",
                            f"Short put ITM ({dte} DTE) — accept assignment per wheel plan (-> HOLDING_SHARES).",
                            urgency="normal", metrics=metrics)
            return _rec(strategy_view, st, AIAction.ROLL, "put_tested_roll_down_out",
                        f"ה-PUT השורט נבחן עם {dte} ימים — גלגול למטה והחוצה (או קבלת הקצאה אם אין קרדיט).",
                        f"Short put tested at {dte} DTE — roll down & out (or accept assignment if no credit).",
                        urgency="normal", metrics=metrics,
                        roll_hint={"style": "down_and_out", "min_dte": 21, "max_dte": 45,
                                   "target_delta": 0.25, "max_strike": short_put["strike"],
                                   "require_net_credit": True, "fallback": "accept_assignment"})
    return _rec(strategy_view, st, AIAction.HOLD, "within_plan",
                f"בתוך התוכנית: נגבו {cap_txt} מהקרדיט, {dte} ימים לפקיעה.",
                f"Within plan: {cap_txt} of credit captured, {dte} DTE.", urgency="low", metrics=metrics)
