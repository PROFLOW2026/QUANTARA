"""Hard deterministic risk gate for QUANTARA Options, executed BEFORE any simulated fill.

Neither Rules mode nor the AI can bypass it. Every check appends a machine-readable reason
(`code: detail`) so the audit log shows exactly why a trade was blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from quantara_options.contracts import (
    AccountRuleProfile,
    DataFeedTier,
    LegRole,
    OptionAccountSummary,
    OptionChainResponse,
    OptionQuoteSnapshot,
    OptionSide,
    StrategyCandidate,
    StrategyType,
)
from quantara_options.strategies.catalog import CREDIT_VERTICALS, STOCK_BACKED, STRATEGY_CATALOG


@dataclass
class RiskGuardConfig:
    max_spread_pct: float = 0.25
    fixture_max_spread_pct: float = 0.60
    allow_fixture_spread_override: bool = True
    allow_stale_quotes: bool = False
    # (upper_equity_bound, multiplier) checked in order. Multipliers > 1 only take effect when
    # `cap_at_profile_pct` is False, because the simulation engine enforces the profile's
    # `max_risk_per_trade_pct` itself and would reject anything above it.
    capital_tier_risk_multipliers: tuple[tuple[float, float], ...] = (
        (7_500.0, 1.0), (15_000.0, 1.0), (float("inf"), 1.0))
    profile_sensitivity: dict[str, float] = field(default_factory=lambda: {
        "conservative": 0.6, "sim_flexible": 1.0, "balanced": 1.0, "aggressive": 1.4})
    cap_at_profile_pct: bool = True
    absolute_max_risk_pct: float = 12.0
    # Max loss is re-estimated at natural prices (buy @ ask, sell @ bid) like the fill engine.
    include_spread_in_risk: bool = True
    stock_backed_stress_move_pct: float = 0.15
    max_qty_per_trade: int = 10
    fee_buffer_pct: float = 0.02
    strike_tolerance: Decimal = Decimal("0.001")


@dataclass
class RiskCheckResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    effective_risk_usd: float = 0.0
    risk_budget_usd: float = 0.0
    cash_required_usd: float = 0.0
    fees_estimate_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "checks": self.checks,
            "effective_risk_usd": round(self.effective_risk_usd, 2),
            "risk_budget_usd": round(self.risk_budget_usd, 2),
            "cash_required_usd": round(self.cash_required_usd, 2),
            "fees_estimate_usd": round(self.fees_estimate_usd, 2),
        }


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ev(v: Any) -> Any:
    return getattr(v, "value", v)


def _iso(v: Any) -> str:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v or "")[:10]


class OptionsRiskGuard:
    def __init__(self, config: Optional[RiskGuardConfig] = None) -> None:
        self.config = config or RiskGuardConfig()

    # ------------------------------------------------------------ sizing helpers

    def tier_multiplier(self, equity: float) -> float:
        for bound, mult in self.config.capital_tier_risk_multipliers:
            if equity <= bound:
                return mult
        return 1.0

    def risk_budget_usd(self, account: OptionAccountSummary) -> float:
        prof = account.rule_profile
        equity = max(0.0, float(account.equity))
        sens = self.config.profile_sensitivity.get(prof.profile_name, 1.0)
        pct = float(prof.max_risk_per_trade_pct) * self.tier_multiplier(equity) * sens
        if self.config.cap_at_profile_pct:
            pct = min(pct, float(prof.max_risk_per_trade_pct))
        pct = min(pct, self.config.absolute_max_risk_pct)
        return equity * pct / 100.0

    @staticmethod
    def natural_spread_cost_usd(candidate: StrategyCandidate) -> float:
        """Extra cost of filling every option leg at the natural price instead of mid."""
        total = 0.0
        for l in candidate.legs:
            if not l.occ_symbol or l.bid is None or l.ask is None:
                continue
            half = max(0.0, (float(l.ask) - float(l.bid)) / 2.0)
            total += half * l.multiplier * candidate.contracts_qty * max(1, l.ratio)
        return total

    def fees_estimate(self, candidate: StrategyCandidate, profile: AccountRuleProfile) -> float:
        option_legs = sum(max(1, l.ratio) for l in candidate.legs if l.occ_symbol)
        per = float(profile.commission_per_contract_usd + profile.clearing_fee_per_contract_usd)
        return round(option_legs * candidate.contracts_qty * per, 2)

    def effective_risk_usd(self, candidate: StrategyCandidate) -> float:
        """Max loss used for the per-trade budget.

        Defined-risk structures use `max_loss_usd`. Stock-backed income trades (CSP / covered
        call / wheel) would show "stock to zero" as max loss, so a stressed gap move is used
        instead; their capital usage is controlled by the collateral and free-cash checks.
        """
        max_loss = float(candidate.max_loss_usd)
        if candidate.strategy_type not in STOCK_BACKED:
            if self.config.include_spread_in_risk:
                max_loss += self.natural_spread_cost_usd(candidate)
            return max_loss
        stress = self.config.stock_backed_stress_move_pct
        credit = max(0.0, sum(
            float(l.mid or 0) * l.multiplier * candidate.contracts_qty * (1 if l.side == OptionSide.SHORT else -1)
            for l in candidate.legs if l.occ_symbol))
        has_stock_leg = any(l.leg_role == LegRole.UNDERLYING_STOCK for l in candidate.legs)
        short_put = next((l for l in candidate.legs if l.leg_role == LegRole.SHORT_PUT), None)
        # Economic risk for CSP/Wheel: use the larger of a 15% stress and 25% of
        # cash-secured notional (minus credit), capped by catalog max_loss.
        econ_floor = 0.25
        if short_put is not None and short_put.strike is not None:
            notional = float(short_put.strike) * short_put.multiplier * candidate.contracts_qty
            stressed = max(stress, econ_floor) * notional - credit
            return max(0.0, min(max_loss, stressed))
        if has_stock_leg:
            stock = next(l for l in candidate.legs if l.leg_role == LegRole.UNDERLYING_STOCK)
            notional = float(stock.mid or candidate.underlying_price) * stock.multiplier * candidate.contracts_qty
            stressed = max(stress, econ_floor) * notional - credit
            return max(0.0, min(max_loss, stressed))
        return 0.0

    def collateral_estimate_usd(self, candidate: StrategyCandidate) -> float:
        """Collateral as the sim reserves it: credit verticals / iron condors lock the FULL width
        (the credit lands in free cash), cash-secured puts lock strike x multiplier x qty."""
        coll = float(candidate.collateral_required_usd)
        if candidate.strategy_type in CREDIT_VERTICALS or candidate.strategy_type == StrategyType.IRON_CONDOR:
            credit = max(0.0, float(candidate.estimated_net_premium_total))
            coll = max(coll, float(candidate.max_loss_usd) + credit)
        return coll

    def _credit_at_natural(self, candidate: StrategyCandidate) -> float:
        credit = max(0.0, float(candidate.estimated_net_premium_total))
        if credit <= 0 or float(candidate.collateral_required_usd) <= 0:
            return 0.0
        return max(0.0, credit - self.natural_spread_cost_usd(candidate))

    def suggest_contracts_qty(self, candidate: StrategyCandidate, account: OptionAccountSummary,
                              *, max_qty: Optional[int] = None) -> int:
        """Largest qty whose risk, cash and collateral fit the account (>= 0)."""
        if candidate.skills_evidence.get("covered_by_existing_shares"):
            return max(1, candidate.contracts_qty)
        units = max(1, candidate.contracts_qty)
        risk_u = self.effective_risk_usd(candidate) / units
        debit_u = max(0.0, -float(candidate.estimated_net_premium_total)) / units
        coll_u = self.collateral_estimate_usd(candidate) / units
        fee_u = self.fees_estimate(candidate, account.rule_profile) / units
        budget = self.risk_budget_usd(account)
        prof = account.rule_profile
        coll_room = float(prof.max_collateral_pct) / 100.0 * float(account.equity) - float(account.cash_reserved)
        caps = [max_qty or self.config.max_qty_per_trade]
        if risk_u > 0:
            caps.append(int(budget // risk_u))
        cash_u = debit_u + coll_u + fee_u - self._credit_at_natural(candidate) / units
        if cash_u > 0:
            caps.append(int(float(account.free_cash) // cash_u))
        if coll_u > 0:
            caps.append(int(max(0.0, coll_room) // coll_u))
        return max(0, min(caps))

    # ------------------------------------------------------------ open validation

    def validate_open(
        self,
        candidate: StrategyCandidate,
        *,
        account: OptionAccountSummary,
        chain: Optional[OptionChainResponse] = None,
        quotes: Optional[Mapping[str, OptionQuoteSnapshot]] = None,
        known_candidate_ids: Optional[Iterable[str]] = None,
        open_strategies: Iterable[Any] = (),
        daily_pnl_usd: Optional[float] = None,
        peak_equity_usd: Optional[float] = None,
        is_roll: bool = False,
        rolling_group_id: Optional[str] = None,
        released_collateral_usd: float = 0.0,
        now: Optional[datetime] = None,
    ) -> RiskCheckResult:
        cfg = self.config
        prof = account.rule_profile
        res = RiskCheckResult(passed=True)
        reasons = res.reasons
        checks = res.checks
        equity = float(account.equity or 0.0)
        open_list = list(open_strategies)

        # 1. Grounding -------------------------------------------------------------
        if known_candidate_ids is not None and candidate.candidate_id not in set(known_candidate_ids):
            reasons.append(f"ungrounded_candidate: {candidate.candidate_id} not produced by scanner this cycle")
        index: dict[str, OptionQuoteSnapshot] = dict(quotes or {})
        if chain is not None:
            for q in chain.contracts:
                index.setdefault(q.contract.occ_symbol, q)
        option_legs = [l for l in candidate.legs if l.leg_role != LegRole.UNDERLYING_STOCK]
        if not option_legs:
            reasons.append("no_option_legs: candidate has no option legs")
        if candidate.contracts_qty < 1 or candidate.contracts_qty > cfg.max_qty_per_trade:
            reasons.append(f"bad_qty: {candidate.contracts_qty}")
        fixture = (chain is not None and chain.feed_tier == DataFeedTier.FIXTURE) or \
            candidate.feed_tier == DataFeedTier.FIXTURE
        max_spread = cfg.fixture_max_spread_pct if (fixture and cfg.allow_fixture_spread_override) \
            else cfg.max_spread_pct
        for leg in candidate.legs:
            if leg.leg_role == LegRole.UNDERLYING_STOCK:
                if leg.occ_symbol is not None:
                    reasons.append("bad_stock_leg: stock leg must not carry an OCC symbol")
                continue
            if not leg.occ_symbol:
                reasons.append(f"missing_occ: {leg.leg_role.value}")
                continue
            q = index.get(leg.occ_symbol)
            if q is None:
                reasons.append(f"unknown_occ: {leg.occ_symbol} not in current valid chain")
                continue
            c = q.contract
            if c.underlying_symbol.upper() != candidate.underlying_symbol.upper() \
                    or (leg.right is not None and c.right != leg.right) \
                    or (leg.strike is not None and abs(Decimal(c.strike) - Decimal(leg.strike)) > cfg.strike_tolerance) \
                    or (leg.expiration_date is not None and c.expiration_date != leg.expiration_date):
                reasons.append(f"contract_mismatch: {leg.occ_symbol} legs disagree with chain contract")
            if int(c.multiplier) != int(leg.multiplier):
                reasons.append(f"multiplier_mismatch: {leg.occ_symbol} {leg.multiplier} vs {c.multiplier}")
            if not c.tradable:
                reasons.append(f"not_tradable: {leg.occ_symbol}")
            # 2. Quote quality ------------------------------------------------------
            bid = float(q.bid) if q.bid is not None else None
            ask = float(q.ask) if q.ask is not None else None
            if bid is None or bid <= 0:
                reasons.append(f"no_bid: {leg.occ_symbol}")
            if ask is None or ask <= 0:
                reasons.append(f"no_ask: {leg.occ_symbol}")
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                if ask < bid:
                    reasons.append(f"crossed_quote: {leg.occ_symbol}")
                mid = (bid + ask) / 2.0
                spread = (ask - bid) / mid if mid > 0 else 9.9
                if spread > max_spread:
                    reasons.append(f"wide_spread: {leg.occ_symbol} {spread:.0%} > {max_spread:.0%}")
            if q.is_stale and not cfg.allow_stale_quotes and not fixture:
                reasons.append(f"stale_quote: {leg.occ_symbol}")
        checks["fixture_spread_override"] = bool(fixture and cfg.allow_fixture_spread_override)

        # 3. Account permissions -----------------------------------------------------
        spec = STRATEGY_CATALOG[candidate.strategy_type]
        if spec.min_approval_level > int(prof.options_approval_level):
            reasons.append(f"approval_level: {candidate.strategy_type.value} needs level "
                           f"{spec.min_approval_level}, account has {prof.options_approval_level}")
        has_naked_short = self._has_naked_short(candidate)
        if has_naked_short and int(prof.options_approval_level) < 4:
            reasons.append("naked_short: uncovered short option requires approval level 4")
        if has_naked_short and not prof.margin_enabled:
            reasons.append("margin_disabled: uncovered short requires margin")
        if spec.is_credit and not prof.allow_earnings_short_premium:
            sk = candidate.skills_evidence.get("skills") or {}
            if sk.get("earnings_flag") or "earnings_event_short_premium" in candidate.warnings:
                reasons.append("earnings_short_premium: short premium into earnings disabled by profile")
        checks["margin_enabled"] = prof.margin_enabled
        checks["approval_level"] = prof.options_approval_level

        # 4. Cash / collateral -------------------------------------------------------
        fees = self.fees_estimate(candidate, prof)
        net_total = float(candidate.estimated_net_premium_total)
        debit = max(0.0, -net_total)
        collateral = self.collateral_estimate_usd(candidate)
        if candidate.skills_evidence.get("covered_by_existing_shares"):
            shares = int(candidate.skills_evidence.get("shares_held") or 0)
            need_shares = sum(l.multiplier * candidate.contracts_qty for l in option_legs
                              if l.leg_role == LegRole.SHORT_CALL)
            if shares < need_shares:
                reasons.append(f"insufficient_shares: hold {shares}, need {need_shares}")
        cash_required = (debit * (1.0 + cfg.fee_buffer_pct) + fees + collateral
                         - self._credit_at_natural(candidate) - released_collateral_usd)
        res.cash_required_usd = cash_required
        res.fees_estimate_usd = fees
        if cash_required > float(account.free_cash) + 1e-6:
            reasons.append(f"insufficient_free_cash: need ${cash_required:,.2f}, free ${account.free_cash:,.2f}")

        # 5. Per-trade risk ----------------------------------------------------------
        eff_risk = self.effective_risk_usd(candidate)
        budget = self.risk_budget_usd(account)
        res.effective_risk_usd, res.risk_budget_usd = eff_risk, budget
        if eff_risk > budget + 1e-6:
            reasons.append(f"max_risk_per_trade: risk ${eff_risk:,.2f} > budget ${budget:,.2f} "
                           f"({float(prof.max_risk_per_trade_pct):g}% x tier {self.tier_multiplier(equity):g})")
        if candidate.strategy_type not in STOCK_BACKED and float(candidate.max_loss_usd) > \
                equity * cfg.absolute_max_risk_pct / 100.0:
            reasons.append(f"absolute_risk_cap: max loss ${float(candidate.max_loss_usd):,.0f} > "
                           f"{cfg.absolute_max_risk_pct:g}% of equity")

        # 6. Portfolio limits --------------------------------------------------------
        n_open = len(open_list) if open_list else int(account.open_strategies_count)
        if not is_roll and n_open + 1 > int(prof.max_open_strategies):
            reasons.append(f"max_open_strategies: {n_open} open, limit {prof.max_open_strategies}")
        if equity > 0:
            coll_after = float(account.cash_reserved) + collateral - released_collateral_usd
            coll_pct = 100.0 * coll_after / equity
            checks["collateral_pct_after"] = round(coll_pct, 2)
            if collateral > 0 and coll_pct > float(prof.max_collateral_pct) + 1e-9:
                reasons.append(f"max_collateral_pct: {coll_pct:.1f}% > {float(prof.max_collateral_pct):g}%")
        if daily_pnl_usd is not None and equity > 0 and not is_roll:
            if daily_pnl_usd <= -float(prof.max_daily_loss_pct) / 100.0 * equity:
                reasons.append(f"max_daily_loss: day P&L ${daily_pnl_usd:,.2f} breaches "
                               f"{float(prof.max_daily_loss_pct):g}%")
        dd_pct = None
        if peak_equity_usd and peak_equity_usd > 0:
            dd_pct = 100.0 * max(0.0, peak_equity_usd - equity) / peak_equity_usd
        elif account.starting_capital and equity < account.starting_capital:
            dd_pct = 100.0 * (account.starting_capital - equity) / account.starting_capital
        checks["current_drawdown_pct"] = round(dd_pct, 2) if dd_pct is not None else None
        if dd_pct is not None and dd_pct >= float(prof.max_drawdown_pct) and not is_roll:
            reasons.append(f"max_drawdown: {dd_pct:.1f}% >= {float(prof.max_drawdown_pct):g}%")

        # 7. Duplicates ----------------------------------------------------------------
        key = (candidate.underlying_symbol.upper(), candidate.strategy_type.value,
               candidate.expiration_date.isoformat())
        same_underlying = 0
        for g in open_list:
            gid = _attr(g, "id")
            if is_roll and rolling_group_id and str(gid) == str(rolling_group_id):
                continue
            gkey = (str(_attr(g, "underlying_symbol", "")).upper(), str(_ev(_attr(g, "strategy_type"))),
                    _iso(_attr(g, "expiration_date")))
            if gkey == key:
                reasons.append(f"duplicate_strategy: {key[1]} {key[0]} {key[2]} already open ({gid})")
                break
            if str(_attr(g, "underlying_symbol", "")).upper() == candidate.underlying_symbol.upper():
                same_underlying += 1
        checks["open_same_underlying"] = same_underlying
        if not is_roll and same_underlying >= 2:
            reasons.append(
                f"underlying_concentration: {same_underlying} open on {candidate.underlying_symbol} (limit 2)"
            )

        # 7b. Natural credit / width re-check at execution (scanner may have used older mid).
        from quantara_options.policy import min_natural_credit_to_width
        ev = candidate.skills_evidence or {}
        width = ev.get("wing_width")
        nat = ev.get("natural_net_after_fees_per_share")
        if (
            candidate.strategy_type in CREDIT_VERTICALS
            or candidate.strategy_type == StrategyType.IRON_CONDOR
        ) and width and nat is not None:
            floor = min_natural_credit_to_width(candidate.strategy_type)
            checks["natural_credit_to_width"] = round(float(nat) / float(width), 4) if float(width) else None
            if float(nat) < float(width) * floor:
                reasons.append(
                    f"natural_credit_too_thin: {float(nat):.3f}/{float(width):.2f} "
                    f"< {floor:.0%} after costs"
                )

        # 7c. Long debit size vs equity (directional).
        if candidate.strategy_type in (StrategyType.LONG_CALL, StrategyType.LONG_PUT) and equity > 0:
            debit_usd = max(0.0, -float(candidate.estimated_net_premium_total))
            max_debit_pct = 3.0 if equity < 7500 else 4.0
            checks["debit_pct_of_equity"] = round(100.0 * debit_usd / equity, 2)
            if debit_usd > equity * max_debit_pct / 100.0:
                reasons.append(
                    f"long_debit_too_large: ${debit_usd:,.0f} > {max_debit_pct:g}% of equity"
                )

        # 8. PDT (only matters when the profile enforces it) ------------------------------
        checks["pdt_enforced"] = prof.enforce_pdt_rule
        res.passed = not reasons
        return res

    @staticmethod
    def _has_naked_short(candidate: StrategyCandidate) -> bool:
        legs = [l for l in candidate.legs if l.occ_symbol]
        shorts = [l for l in legs if l.side == OptionSide.SHORT]
        longs = [l for l in legs if l.side == OptionSide.LONG]
        stock = any(l.leg_role == LegRole.UNDERLYING_STOCK for l in candidate.legs) or \
            bool(candidate.skills_evidence.get("covered_by_existing_shares"))
        for s in shorts:
            if s.right is None:
                continue
            if s.right.value == "put" and candidate.strategy_type in (StrategyType.CASH_SECURED_PUT,
                                                                      StrategyType.WHEEL):
                continue
            if s.right.value == "call" and stock:
                continue
            covered = any(l.right == s.right and l.expiration_date == s.expiration_date for l in longs)
            if not covered:
                return True
        return False

    # ------------------------------------------------------------ close validation

    def validate_close(
        self,
        strategy_view: Any,
        *,
        account: OptionAccountSummary,
        quotes: Optional[Mapping[str, OptionQuoteSnapshot]] = None,
        day_trades_last_5d: int = 0,
        mandatory: bool = False,
        now: Optional[datetime] = None,
    ) -> RiskCheckResult:
        """Closing reduces risk, so only data-quality and opt-in PDT rules can block it."""
        now = now or datetime.now(timezone.utc)
        res = RiskCheckResult(passed=True)
        prof = account.rule_profile
        if str(_ev(_attr(strategy_view, "status", "open"))).lower() != "open":
            res.reasons.append("not_open: group is not open")
        missing = []
        for leg in _attr(strategy_view, "legs", []) or []:
            occ = _attr(leg, "occ_symbol")
            if occ and quotes is not None and occ not in quotes and _attr(leg, "current_mark") is None:
                missing.append(occ)
        if missing:
            res.reasons.append(f"no_mark: {', '.join(missing[:4])}")
        opened = _attr(strategy_view, "opened_at")
        if prof.enforce_pdt_rule and not mandatory and isinstance(opened, datetime):
            same_day = opened.astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date()
            if same_day and float(account.equity) < float(prof.pdt_equity_threshold_usd) \
                    and day_trades_last_5d >= 3:
                res.reasons.append(f"pdt_rule: {day_trades_last_5d} day trades in 5 days and equity < "
                                   f"${float(prof.pdt_equity_threshold_usd):,.0f}")
        res.checks["pdt_enforced"] = prof.enforce_pdt_rule
        res.passed = not res.reasons
        return res
