"""Deterministic options opportunity scanner.

Filters a chain down to tradable quotes (bid/ask present, spread, OI/volume, DTE, staleness),
then builds fully priced `StrategyCandidate`s for every allowed strategy with net greeks,
max profit/loss, collateral, break-evens, PoP, payoff curve and a deterministic `rules_score`.
Everything is per-contract `multiplier` aware.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional

from quantara_options.contracts import (
    DataFeedTier,
    ExerciseStyle,
    Greeks,
    LegPlan,
    LegRole,
    OptionChainResponse,
    OptionQuoteSnapshot,
    OptionRight,
    OptionSide,
    StrategyCandidate,
    StrategyType,
    WheelState,
)
from quantara_options.math_pricing import bsm_greeks, compute_payoff_curve, implied_volatility, norm_cdf
from quantara_options.strategies.catalog import (
    CREDIT_VERTICALS,
    STRATEGY_CATALOG,
    WING_WIDTH_MAX_USD,
    WING_WIDTH_MIN_USD,
    DeltaBand,
    StrategySpec,
    wheel_leg_for_state,
    wing_width_for_capital,
)

CENT = Decimal("0.01")


def _q2(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class ScannerConfig:
    min_open_interest: int = 10
    min_volume: int = 0
    max_spread_pct: float = 0.25
    fixture_max_spread_pct: float = 0.60
    allow_fixture_spread_override: bool = True
    allow_stale_quotes: bool = False
    risk_free_rate: float = 0.045
    max_expirations_per_strategy: int = 2
    max_candidates_per_strategy: int = 2
    min_credit_to_width: float = 0.15
    max_debit_to_width: float = 0.70
    min_credit_per_share: float = 0.10
    min_debit_per_share: float = 0.05
    payoff_points: int = 21
    default_contracts_qty: int = 1


@dataclass
class ScanContext:
    """Per-account context that changes which candidates are valid."""

    account_equity: float = 10_000.0
    free_cash: Optional[float] = None
    approval_level: int = 3
    shares_held: dict[str, int] = field(default_factory=dict)
    share_cost_basis: dict[str, float] = field(default_factory=dict)
    wheel_states: dict[str, str] = field(default_factory=dict)
    market_bias: dict[str, float] = field(default_factory=dict)  # symbol -> [-1, 1]
    iv_rank: dict[str, float] = field(default_factory=dict)      # symbol -> [0, 100]
    skills_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    allow_buy_write: bool = True


@dataclass
class _Q:
    """A filtered, enriched quote used internally by the scanner."""

    quote: OptionQuoteSnapshot
    occ: str
    right: OptionRight
    strike: float
    expiration: date
    dte: int
    multiplier: int
    bid: float
    ask: float
    mid: float
    spread_pct: float
    delta: float
    iv: float
    greeks: Greeks
    oi: int
    volume: int


class OptionsOpportunityScanner:
    def __init__(self, config: Optional[ScannerConfig] = None) -> None:
        self.config = config or ScannerConfig()
        self.last_rejections: dict[str, int] = {}
        self.scan_errors: dict[str, str] = {}

    # ------------------------------------------------------------ public API

    def scan(
        self,
        chain: OptionChainResponse,
        *,
        allowed_strategies: Optional[Iterable[StrategyType]] = None,
        context: Optional[ScanContext] = None,
        max_candidates: Optional[int] = None,
    ) -> list[StrategyCandidate]:
        ctx = context or ScanContext()
        spot = float(chain.underlying_price or 0)
        if spot <= 0 or not chain.contracts:
            return []
        if chain.is_stale and not self.config.allow_stale_quotes and chain.feed_tier != DataFeedTier.FIXTURE:
            self.last_rejections = {"chain_stale": 1}
            return []
        allowed = list(allowed_strategies) if allowed_strategies is not None else list(STRATEGY_CATALOG)
        allowed = [StrategyType(s) for s in allowed
                   if STRATEGY_CATALOG[StrategyType(s)].min_approval_level <= ctx.approval_level]

        quotes = self._usable_quotes(chain)
        if not quotes:
            return []
        by_exp: dict[date, list[_Q]] = {}
        for q in quotes:
            by_exp.setdefault(q.expiration, []).append(q)
        atm_iv = self._atm_iv(quotes, spot)

        out: list[StrategyCandidate] = []
        for st in allowed:
            spec = STRATEGY_CATALOG[st]
            exps = self._pick_expirations(by_exp, spec)
            built: list[StrategyCandidate] = []
            for exp in exps:
                built.extend(self._build(st, spec, chain, spot, by_exp[exp], ctx, atm_iv))
            built.sort(key=lambda c: c.rules_score, reverse=True)
            out.extend(built[: self.config.max_candidates_per_strategy])

        out.sort(key=lambda c: (-c.rules_score, c.candidate_id))
        return out[:max_candidates] if max_candidates else out

    def scan_universe(
        self,
        chains: Mapping[str, OptionChainResponse] | Iterable[OptionChainResponse],
        *,
        allowed_strategies: Optional[Iterable[StrategyType]] = None,
        context: Optional[ScanContext] = None,
        max_candidates: Optional[int] = None,
    ) -> list[StrategyCandidate]:
        items = chains.values() if isinstance(chains, Mapping) else chains
        allowed = list(allowed_strategies) if allowed_strategies is not None else None
        out: list[StrategyCandidate] = []
        for chain in items:
            try:
                out.extend(self.scan(chain, allowed_strategies=allowed, context=context))
            except Exception as exc:  # one bad chain never kills the universe scan
                self.scan_errors[chain.underlying_symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
        out.sort(key=lambda c: (-c.rules_score, c.candidate_id))
        return out[:max_candidates] if max_candidates else out

    # ------------------------------------------------------------ filtering

    def _max_spread(self, chain: OptionChainResponse) -> float:
        if chain.feed_tier == DataFeedTier.FIXTURE and self.config.allow_fixture_spread_override:
            return self.config.fixture_max_spread_pct
        return self.config.max_spread_pct

    def _usable_quotes(self, chain: OptionChainResponse) -> list[_Q]:
        cfg = self.config
        spot = float(chain.underlying_price or 0)
        today = chain.as_of.date()
        max_spread = self._max_spread(chain)
        rej: dict[str, int] = {}
        out: list[_Q] = []

        def reject(reason: str) -> None:
            rej[reason] = rej.get(reason, 0) + 1

        for q in chain.contracts:
            c = q.contract
            if not c.tradable:
                reject("not_tradable")
                continue
            if q.is_stale and not cfg.allow_stale_quotes and chain.feed_tier != DataFeedTier.FIXTURE:
                reject("stale_quote")
                continue
            bid = float(q.bid) if q.bid is not None else None
            ask = float(q.ask) if q.ask is not None else None
            if bid is None or bid <= 0:
                reject("no_bid")
                continue
            if ask is None or ask <= 0 or ask < bid:
                reject("no_ask")
                continue
            mid = float(q.mid) if q.mid is not None and float(q.mid) > 0 else (bid + ask) / 2.0
            # Fraction of mid; the quote's own `spread_pct` field is expressed in percent.
            spread_pct = (ask - bid) / ((ask + bid) / 2.0)
            if spread_pct > max_spread:
                reject("wide_spread")
                continue
            oi = max(int(q.open_interest or 0), int(c.open_interest or 0))
            vol = int(q.volume or 0)
            if oi < cfg.min_open_interest and vol < max(1, cfg.min_volume):
                reject("low_oi")
                continue
            if vol < cfg.min_volume:
                reject("low_volume")
                continue
            mult = int(c.multiplier or 0)
            if mult <= 0:
                reject("bad_multiplier")
                continue
            dte = (c.expiration_date - today).days
            if dte < 1:
                reject("expiring")
                continue
            strike = float(c.strike)
            t = max(dte, 1) / 365.0
            iv = q.implied_volatility
            if iv is None or iv <= 0:
                iv = implied_volatility(market_price=mid, spot=spot, strike=strike, t_years=t,
                                        rate=cfg.risk_free_rate, right=c.right,
                                        exercise_style=ExerciseStyle.EUROPEAN)
            iv = float(iv) if iv else 0.30
            greeks = q.greeks if (q.greeks and q.greeks.delta is not None) else bsm_greeks(
                spot=spot, strike=strike, t_years=t, rate=cfg.risk_free_rate, sigma=iv, right=c.right)
            out.append(_Q(quote=q, occ=c.occ_symbol, right=c.right, strike=strike,
                          expiration=c.expiration_date, dte=dte, multiplier=mult, bid=bid, ask=ask,
                          mid=mid, spread_pct=float(spread_pct), delta=float(greeks.delta or 0.0),
                          iv=iv, greeks=greeks, oi=oi, volume=vol))
        self.last_rejections = rej
        return out

    @staticmethod
    def _atm_iv(quotes: list[_Q], spot: float) -> Optional[float]:
        near = sorted(quotes, key=lambda q: (abs(q.strike - spot), q.dte))[:4]
        ivs = [q.iv for q in near if q.iv]
        return round(sum(ivs) / len(ivs), 4) if ivs else None

    def _pick_expirations(self, by_exp: dict[date, list[_Q]], spec: StrategySpec) -> list[date]:
        eligible = []
        for exp, qs in by_exp.items():
            dte = qs[0].dte
            if spec.dte_min <= dte <= spec.dte_max:
                eligible.append((abs(dte - spec.dte_target), exp))
        eligible.sort()
        return [e for _, e in eligible[: max(1, self.config.max_expirations_per_strategy)]]

    @staticmethod
    def _ranked_by_delta(qs: list[_Q], right: OptionRight, band: DeltaBand, extra: Any = None) -> list[_Q]:
        pool = [q for q in qs if q.right == right and band.contains(q.delta)]
        if extra is not None:
            pool = [q for q in pool if extra(q)]
        return sorted(pool, key=lambda q: (abs(abs(q.delta) - band.target), q.spread_pct, q.occ))

    def _pick_by_delta(self, qs: list[_Q], right: OptionRight, band: DeltaBand,
                       extra: Any = None) -> Optional[_Q]:
        ranked = self._ranked_by_delta(qs, right, band, extra)
        return ranked[0] if ranked else None

    @staticmethod
    def _wing_options(qs: list[_Q], short: _Q, width_target: float, direction: int) -> list[_Q]:
        """Wing strikes ordered by preference: target width first, then narrower, then wider.

        direction=-1 -> lower strike (put wing), +1 -> higher strike (call wing).
        """
        pool = []
        for q in qs:
            if q.right != short.right or q.multiplier != short.multiplier:
                continue
            width = (q.strike - short.strike) * direction
            if WING_WIDTH_MIN_USD - 1e-9 <= width <= WING_WIDTH_MAX_USD + 1e-9:
                pool.append(q)

        def key(q: _Q) -> tuple[int, float, float]:
            w = abs(q.strike - short.strike)
            return (0 if w <= width_target + 1e-9 else 1, abs(w - width_target), q.spread_pct)

        return sorted(pool, key=key)[:4]

    # ------------------------------------------------------------ builders

    def _build(self, st: StrategyType, spec: StrategySpec, chain: OptionChainResponse, spot: float,
               qs: list[_Q], ctx: ScanContext, atm_iv: Optional[float]) -> list[StrategyCandidate]:
        sym = chain.underlying_symbol
        legs_rules = {r.leg_role: r for r in spec.legs}
        res: list[StrategyCandidate] = []

        def make(**kw: Any) -> bool:
            cand = self._finalize(st=st, spec=spec, chain=chain, spot=spot, ctx=ctx, atm_iv=atm_iv, **kw)
            if cand is not None:
                res.append(cand)
            return cand is not None

        if st == StrategyType.LONG_CALL:
            q = self._pick_by_delta(qs, OptionRight.CALL, legs_rules[LegRole.LONG_CALL].delta_band)
            if q and q.ask >= self.config.min_debit_per_share:
                make(legs=[(q, LegRole.LONG_CALL, OptionSide.LONG)], width=None)

        elif st == StrategyType.LONG_PUT:
            q = self._pick_by_delta(qs, OptionRight.PUT, legs_rules[LegRole.LONG_PUT].delta_band)
            if q and q.ask >= self.config.min_debit_per_share:
                make(legs=[(q, LegRole.LONG_PUT, OptionSide.LONG)], width=None)

        elif st == StrategyType.CASH_SECURED_PUT:
            q = self._pick_by_delta(qs, OptionRight.PUT, legs_rules[LegRole.SHORT_PUT].delta_band,
                                    extra=lambda x: x.strike < spot)
            if q:
                make(legs=[(q, LegRole.SHORT_PUT, OptionSide.SHORT)], width=None)

        elif st == StrategyType.COVERED_CALL:
            self._build_covered_call(st, spec, qs, spot, ctx, sym, make, wheel=False)

        elif st == StrategyType.WHEEL:
            state = WheelState(ctx.wheel_states.get(sym, WheelState.IDLE.value))
            role = wheel_leg_for_state(state)
            if role == LegRole.SHORT_PUT:
                q = self._pick_by_delta(qs, OptionRight.PUT, legs_rules[LegRole.SHORT_PUT].delta_band,
                                        extra=lambda x: x.strike < spot)
                if q:
                    make(legs=[(q, LegRole.SHORT_PUT, OptionSide.SHORT)], width=None,
                         extra_evidence={"wheel_state": state.value, "wheel_next_state": "short_put"})
            elif role == LegRole.SHORT_CALL:
                self._build_covered_call(st, spec, qs, spot, ctx, sym, make, wheel=True)

        elif st in (StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_PUT_SPREAD):
            right = OptionRight.CALL if st == StrategyType.BULL_CALL_SPREAD else OptionRight.PUT
            long_role = LegRole.LONG_CALL if right == OptionRight.CALL else LegRole.LONG_PUT
            short_role = LegRole.SHORT_CALL if right == OptionRight.CALL else LegRole.SHORT_PUT
            lq = self._pick_by_delta(qs, right, legs_rules[long_role].delta_band)
            if lq:
                direction = 1 if right == OptionRight.CALL else -1
                max_w = max(WING_WIDTH_MAX_USD, wing_width_for_capital(ctx.account_equity, spot) * 2)
                sq = self._pick_by_delta(
                    qs, right, legs_rules[short_role].delta_band,
                    extra=lambda x: x.multiplier == lq.multiplier
                    and 0 < (x.strike - lq.strike) * direction <= max_w)
                if sq:
                    make(legs=[(lq, long_role, OptionSide.LONG), (sq, short_role, OptionSide.SHORT)],
                         width=abs(sq.strike - lq.strike))

        elif st in CREDIT_VERTICALS:
            right = OptionRight.PUT if st == StrategyType.BULL_PUT_CREDIT_SPREAD else OptionRight.CALL
            short_role = LegRole.SHORT_PUT if right == OptionRight.PUT else LegRole.SHORT_CALL
            long_role = LegRole.LONG_PUT if right == OptionRight.PUT else LegRole.LONG_CALL
            direction = -1 if right == OptionRight.PUT else 1
            otm = (lambda x: x.strike < spot) if right == OptionRight.PUT else (lambda x: x.strike > spot)
            target_w = wing_width_for_capital(ctx.account_equity, spot)
            done = False
            for sq in self._ranked_by_delta(qs, right, legs_rules[short_role].delta_band, extra=otm)[:3]:
                for wq in self._wing_options(qs, sq, target_w, direction):
                    if make(legs=[(sq, short_role, OptionSide.SHORT), (wq, long_role, OptionSide.LONG)],
                            width=abs(sq.strike - wq.strike)):
                        done = True
                        break
                if done:
                    break

        elif st == StrategyType.IRON_CONDOR:
            sp = self._pick_by_delta(qs, OptionRight.PUT, legs_rules[LegRole.SHORT_PUT].delta_band,
                                     extra=lambda x: x.strike < spot)
            sc = self._pick_by_delta(qs, OptionRight.CALL, legs_rules[LegRole.SHORT_CALL].delta_band,
                                     extra=lambda x: x.strike > spot)
            if sp and sc and sp.multiplier == sc.multiplier:
                w = wing_width_for_capital(ctx.account_equity, spot)
                for lp in self._wing_options(qs, sp, w, -1):
                    pw = abs(sp.strike - lp.strike)
                    lcs = self._wing_options(qs, sc, pw, 1)
                    lc = lcs[0] if lcs else None
                    if lc is None:
                        continue
                    cw = abs(lc.strike - sc.strike)
                    if abs(pw - cw) > max(0.5, 0.25 * max(pw, cw)):
                        continue
                    if make(legs=[(lp, LegRole.LONG_PUT, OptionSide.LONG),
                                  (sp, LegRole.SHORT_PUT, OptionSide.SHORT),
                                  (sc, LegRole.SHORT_CALL, OptionSide.SHORT),
                                  (lc, LegRole.LONG_CALL, OptionSide.LONG)],
                            width=max(pw, cw)):
                        break
        return res

    def _build_covered_call(self, st: StrategyType, spec: StrategySpec, qs: list[_Q], spot: float,
                            ctx: ScanContext, sym: str, make: Any, *, wheel: bool) -> None:
        band = next(r.delta_band for r in spec.legs if r.leg_role == LegRole.SHORT_CALL)
        shares = int(ctx.shares_held.get(sym, 0))
        basis = float(ctx.share_cost_basis.get(sym) or 0.0) or None
        floor_strike = max(spot, basis or 0.0) if wheel else spot
        q = self._pick_by_delta(qs, OptionRight.CALL, band, extra=lambda x: x.strike > floor_strike)
        if q is None and wheel and basis:
            pool = [x for x in qs if x.right == OptionRight.CALL and x.strike >= basis and abs(x.delta) >= 0.10]
            q = min(pool, key=lambda x: x.strike) if pool else None
        if q is None:
            return
        owned = shares >= q.multiplier
        if wheel and not owned:
            return
        if not owned and not ctx.allow_buy_write:
            return
        qty = min(10, shares // q.multiplier) if owned else 1
        evidence = {"covered_by_existing_shares": owned, "shares_held": shares,
                    "share_cost_basis": basis}
        if wheel:
            evidence.update({"wheel_state": WheelState.HOLDING_SHARES.value, "wheel_next_state": "short_call",
                             "adjusted_cost_basis": basis})
        make(legs=[(q, LegRole.SHORT_CALL, OptionSide.SHORT)], width=None, qty=qty,
             stock=None if owned else "buy_write", share_basis=basis if owned else spot,
             extra_evidence=evidence)

    # ------------------------------------------------------------ pricing / scoring

    def _finalize(
        self,
        *,
        st: StrategyType,
        spec: StrategySpec,
        chain: OptionChainResponse,
        spot: float,
        ctx: ScanContext,
        atm_iv: Optional[float],
        legs: list[tuple[_Q, LegRole, OptionSide]],
        width: Optional[float],
        qty: Optional[int] = None,
        stock: Optional[str] = None,
        share_basis: Optional[float] = None,
        extra_evidence: Optional[dict[str, Any]] = None,
    ) -> Optional[StrategyCandidate]:
        cfg = self.config
        sym = chain.underlying_symbol
        mult = legs[0][0].multiplier
        if any(q.multiplier != mult for q, _, _ in legs):
            return None
        qty = int(qty or cfg.default_contracts_qty)
        exp = legs[0][0].expiration
        dte = legs[0][0].dte
        warnings: list[str] = []

        # Net option premium per share (+credit / -debit), at mid.
        net = sum((q.mid if side == OptionSide.SHORT else -q.mid) for q, _, side in legs)
        is_credit_opts = net > 0

        k = {role: q.strike for q, role, _ in legs}
        iv_for_pop = atm_iv or (sum(q.iv for q, _, _ in legs) / len(legs))
        t = max(dte, 1) / 365.0

        def p_above(level: float) -> float:
            if level <= 0:
                return 1.0
            sig = max(iv_for_pop, 0.05)
            d2 = (math.log(spot / level) + (cfg.risk_free_rate - 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
            return norm_cdf(d2)

        max_profit: Optional[float]
        bes: list[float]
        collateral = 0.0
        stock_cost = 0.0

        if st == StrategyType.LONG_CALL:
            debit = -net
            max_loss, max_profit = debit, None
            bes = [k[LegRole.LONG_CALL] + debit]
            pop = p_above(bes[0])
        elif st == StrategyType.LONG_PUT:
            debit = -net
            max_loss, max_profit = debit, max(0.0, k[LegRole.LONG_PUT] - debit)
            bes = [k[LegRole.LONG_PUT] - debit]
            pop = 1.0 - p_above(bes[0])
        elif st in (StrategyType.CASH_SECURED_PUT,) or (st == StrategyType.WHEEL and LegRole.SHORT_PUT in k):
            credit = net
            if credit < cfg.min_credit_per_share:
                return None
            ks = k[LegRole.SHORT_PUT]
            max_profit, max_loss = credit, ks - credit
            collateral = ks
            bes = [ks - credit]
            pop = p_above(bes[0])
        elif st in (StrategyType.COVERED_CALL, StrategyType.WHEEL):
            credit = net
            if credit < cfg.min_credit_per_share * 0.5:
                return None
            ks = k[LegRole.SHORT_CALL]
            basis = float(share_basis or spot)
            max_profit = (ks - basis) + credit
            max_loss = max(0.0, basis - credit)
            bes = [basis - credit]
            pop = p_above(bes[0])
            if stock == "buy_write":
                stock_cost = spot
                warnings.append("buy_write_requires_share_purchase")
        elif st in (StrategyType.BULL_CALL_SPREAD, StrategyType.BEAR_PUT_SPREAD):
            debit = -net
            if width is None or debit <= cfg.min_debit_per_share or debit >= width * cfg.max_debit_to_width:
                return None
            max_profit, max_loss = width - debit, debit
            if st == StrategyType.BULL_CALL_SPREAD:
                bes = [k[LegRole.LONG_CALL] + debit]
                pop = p_above(bes[0])
            else:
                bes = [k[LegRole.LONG_PUT] - debit]
                pop = 1.0 - p_above(bes[0])
        elif st in CREDIT_VERTICALS:
            credit = net
            if width is None or credit < cfg.min_credit_per_share or credit < width * cfg.min_credit_to_width:
                return None
            if credit >= width:
                return None
            max_profit, max_loss = credit, width - credit
            collateral = max_loss
            if st == StrategyType.BULL_PUT_CREDIT_SPREAD:
                bes = [k[LegRole.SHORT_PUT] - credit]
                pop = p_above(bes[0])
            else:
                bes = [k[LegRole.SHORT_CALL] + credit]
                pop = 1.0 - p_above(bes[0])
        elif st == StrategyType.IRON_CONDOR:
            credit = net
            if width is None or credit < cfg.min_credit_per_share or credit < width * cfg.min_credit_to_width:
                return None
            if credit >= width:
                return None
            max_profit, max_loss = credit, width - credit
            collateral = max_loss
            bes = [k[LegRole.SHORT_PUT] - credit, k[LegRole.SHORT_CALL] + credit]
            pop = p_above(bes[0]) - p_above(bes[1])
        else:
            return None

        if max_loss <= 0 and st not in (StrategyType.COVERED_CALL, StrategyType.WHEEL):
            return None
        scale = mult * qty

        plan_legs: list[LegPlan] = []
        for q, role, side in legs:
            plan_legs.append(LegPlan(
                occ_symbol=q.occ, underlying_symbol=sym, leg_role=role, side=side, right=q.right,
                strike=q.quote.contract.strike, expiration_date=q.expiration, multiplier=q.multiplier,
                ratio=1, exercise_style=q.quote.contract.exercise_style,
                bid=_q2(q.bid), ask=_q2(q.ask), mid=_q2(q.mid), greeks=q.greeks, implied_volatility=q.iv,
            ))
        if stock == "buy_write":
            plan_legs.insert(0, LegPlan(
                occ_symbol=None, underlying_symbol=sym, leg_role=LegRole.UNDERLYING_STOCK,
                side=OptionSide.LONG, multiplier=mult, ratio=1, mid=_q2(spot), bid=_q2(spot), ask=_q2(spot)))

        # Net greeks, share-equivalent delta and $/day theta, $/1-vol-pt vega for the whole position.
        net_delta = net_theta = net_vega = 0.0
        for q, _, side in legs:
            sgn = 1.0 if side == OptionSide.LONG else -1.0
            net_delta += sgn * float(q.greeks.delta or 0.0) * scale
            net_theta += sgn * float(q.greeks.theta or 0.0) * scale
            net_vega += sgn * float(q.greeks.vega or 0.0) * scale
        owned_shares = bool((extra_evidence or {}).get("covered_by_existing_shares"))
        if stock == "buy_write" or owned_shares:
            net_delta += float(scale)

        liquidity = min(self._liquidity(q) for q, _, _ in legs)
        if chain.is_indicative:
            warnings.append("indicative_quotes")
        if chain.feed_tier == DataFeedTier.FIXTURE:
            warnings.append("fixture_data")
        if any(q.spread_pct > 0.15 for q, _, _ in legs):
            warnings.append("wide_spread_leg")

        net_per_unit = net - stock_cost
        evidence: dict[str, Any] = {"atm_iv": atm_iv, "wing_width": width}
        sk = ctx.skills_evidence.get(sym) or {}
        if sk:
            evidence["skills"] = sk
        if extra_evidence:
            evidence.update(extra_evidence)
        if spec.is_credit and sk.get("earnings_flag"):
            warnings.append("earnings_event_short_premium")

        payoff_legs = list(plan_legs)
        if owned_shares and share_basis:
            payoff_legs.insert(0, LegPlan(occ_symbol=None, underlying_symbol=sym,
                                          leg_role=LegRole.UNDERLYING_STOCK, side=OptionSide.LONG,
                                          multiplier=mult, mid=_q2(share_basis)))
        curve = compute_payoff_curve(legs=payoff_legs, spot=spot, entry_option_cash_flow_usd=net * scale,
                                     contracts_qty=qty, dte_remaining=dte, rate=cfg.risk_free_rate,
                                     default_iv=iv_for_pop, points=cfg.payoff_points)
        evidence["payoff_curve"] = [p.model_dump() for p in curve]

        strikes_key = "/".join(f"{r.value}:{q.strike:g}" for q, r, _ in legs)
        cid_src = f"{st.value}|{sym}|{exp.isoformat()}|{strikes_key}|{'bw' if stock else ''}"
        cid = f"cand_{st.value}_{sym}_{exp.strftime('%y%m%d')}_{hashlib.sha1(cid_src.encode()).hexdigest()[:8]}"

        cand = StrategyCandidate(
            candidate_id=cid,
            strategy_type=st,
            underlying_symbol=sym,
            underlying_price=_q2(spot),
            contracts_qty=qty,
            legs=plan_legs,
            dte=dte,
            expiration_date=exp,
            is_credit=net_per_unit > 0,
            estimated_net_premium_per_unit=_q2(net_per_unit),
            estimated_net_premium_total=_q2(net_per_unit * scale),
            max_profit_usd=_q2(max_profit * scale) if max_profit is not None else None,
            max_loss_usd=_q2(max(0.0, max_loss) * scale),
            collateral_required_usd=_q2(collateral * scale),
            break_even_prices=[_q2(b) for b in bes],
            probability_of_profit=round(_clamp(pop), 4),
            iv_rank=ctx.iv_rank.get(sym),
            net_delta=round(net_delta, 2),
            net_theta=round(net_theta, 2),
            net_vega=round(net_vega, 2),
            liquidity_score=round(liquidity, 4),
            feed_tier=chain.feed_tier,
            is_indicative=chain.is_indicative,
            warnings=warnings,
            skills_evidence=evidence,
        )
        cand.rules_score = self._score(cand, spec, atm_iv, ctx, width, is_credit_opts)
        cand.rationale_summary = self._rationale_he(cand, spec, legs, width)
        return cand

    @staticmethod
    def _liquidity(q: _Q) -> float:
        spread_c = _clamp(1.0 - q.spread_pct / 0.25)
        oi_c = _clamp(math.log10(q.oi + 1) / 3.0)
        vol_c = _clamp(math.log10(q.volume + 1) / 3.0)
        return 0.5 * spread_c + 0.3 * oi_c + 0.2 * vol_c

    @staticmethod
    def _score(c: StrategyCandidate, spec: StrategySpec, atm_iv: Optional[float], ctx: ScanContext,
               width: Optional[float], is_credit_opts: bool) -> float:
        pop = c.probability_of_profit if c.probability_of_profit is not None else 0.5
        if spec.is_credit:
            pop_fit = _clamp((pop - 0.50) / 0.35)
        else:
            pop_fit = _clamp((pop - 0.25) / 0.35)

        max_loss = float(c.max_loss_usd) or 1.0
        if c.max_profit_usd is None:
            rr_fit = 0.6
        elif spec.is_credit and width:
            credit = float(c.estimated_net_premium_per_unit) if is_credit_opts else 0.0
            rr_fit = _clamp((credit / width) / 0.45) if credit > 0 else 0.3
        elif spec.stock_backed:
            rr_fit = _clamp(float(c.max_profit_usd) / max(1.0, float(c.collateral_required_usd or max_loss)) / 0.03)
        else:
            rr_fit = _clamp((float(c.max_profit_usd) / max_loss) / 2.0)

        if c.iv_rank is not None:
            iv_level = _clamp(c.iv_rank / 100.0)
        elif atm_iv is not None:
            iv_level = _clamp((atm_iv - 0.15) / 0.45)
        else:
            iv_level = 0.5
        iv_fit = {"low_mid": 1.0 - iv_level, "mid_high": _clamp(0.3 + iv_level),
                  "high": iv_level, "any": 0.6}.get(spec.iv_regime, 0.5)

        bias = _clamp(float(ctx.market_bias.get(c.underlying_symbol, 0.0)), -1.0, 1.0)
        bias_fit = {"bullish": (1 + bias) / 2, "bearish": (1 - bias) / 2, "neutral": 1 - abs(bias),
                    "neutral_bullish": _clamp(0.6 + 0.4 * bias), "neutral_bearish": _clamp(0.6 - 0.4 * bias),
                    }.get(spec.direction, 0.5)

        dte_fit = _clamp(1.0 - abs(c.dte - spec.dte_target) / max(1, spec.dte_target))

        score = (0.25 * pop_fit + 0.20 * rr_fit + 0.20 * c.liquidity_score
                 + 0.15 * iv_fit + 0.10 * bias_fit + 0.10 * dte_fit)
        if "earnings_event_short_premium" in c.warnings:
            score -= 0.25
        if "wide_spread_leg" in c.warnings:
            score -= 0.05
        if "buy_write_requires_share_purchase" in c.warnings:
            score -= 0.05
        news = (c.skills_evidence.get("skills") or {}).get("news_score")
        if news is not None and float(news) <= -0.6 and spec.direction in {"bullish", "neutral_bullish"}:
            score -= 0.15
        return round(100.0 * _clamp(score), 2)

    @staticmethod
    def _rationale_he(c: StrategyCandidate, spec: StrategySpec, legs: list[tuple[_Q, LegRole, OptionSide]],
                      width: Optional[float]) -> str:
        parts = []
        for q, role, side in legs:
            word = "קנייה" if side == OptionSide.LONG else "מכירה"
            parts.append(f"{word} {q.right.value.upper()} {q.strike:g} (Δ{q.delta:+.2f})")
        prem = float(c.estimated_net_premium_total)
        prem_txt = f"קרדיט ${prem:,.0f}" if prem > 0 else f"דביט ${-prem:,.0f}"
        mp = "בלתי מוגבל" if c.max_profit_usd is None else f"${float(c.max_profit_usd):,.0f}"
        pop = f"{(c.probability_of_profit or 0) * 100:.0f}%"
        txt = (f"{spec.label_he} על {c.underlying_symbol} @ {float(c.underlying_price):,.2f} | "
               f"פקיעה {c.expiration_date.isoformat()} ({c.dte} ימים) | {', '.join(parts)} | {prem_txt} | "
               f"רווח מקס' {mp}, הפסד מקס' ${float(c.max_loss_usd):,.0f}, בטוחה "
               f"${float(c.collateral_required_usd):,.0f} | PoP {pop} | נזילות {c.liquidity_score:.2f} | "
               f"ציון {c.rules_score:.0f}")
        if width:
            txt += f" | רוחב כנף ${width:g}"
        if c.warnings:
            txt += f" | אזהרות: {', '.join(c.warnings)}"
        return txt


def resize_candidate(candidate: StrategyCandidate, qty: int) -> StrategyCandidate:
    """Return a copy re-scaled to `qty` contracts (all totals are linear in qty)."""
    qty = max(1, int(qty))
    old = max(1, int(candidate.contracts_qty))
    if qty == old:
        return candidate.model_copy(deep=True)
    f = Decimal(qty) / Decimal(old)
    data = candidate.model_copy(deep=True)
    data.contracts_qty = qty
    data.estimated_net_premium_total = _q2(candidate.estimated_net_premium_total * f)
    data.max_profit_usd = _q2(candidate.max_profit_usd * f) if candidate.max_profit_usd is not None else None
    data.max_loss_usd = _q2(candidate.max_loss_usd * f)
    data.collateral_required_usd = _q2(candidate.collateral_required_usd * f)
    ratio = float(f)
    data.net_delta = round(candidate.net_delta * ratio, 2)
    data.net_theta = round(candidate.net_theta * ratio, 2)
    data.net_vega = round(candidate.net_vega * ratio, 2)
    curve = data.skills_evidence.get("payoff_curve")
    if isinstance(curve, list):
        data.skills_evidence["payoff_curve"] = [
            {**p, "expiration_pnl": round(p["expiration_pnl"] * ratio, 2), "t0_pnl": round(p["t0_pnl"] * ratio, 2)}
            for p in curve if isinstance(p, dict)
        ]
    return data
