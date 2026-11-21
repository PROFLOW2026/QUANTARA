"""Deterministic, BSM-consistent synthetic options provider (tests / closed market / scenarios).

* ``feed_tier=DataFeedTier.FIXTURE``, ``is_indicative=False``, ``is_delayed_trades=False``.
* Every contract / chain carries the warning ``"synthetic_fixture_not_live_market"``.
* Prices come from Black-Scholes-Merton (``math_pricing.bsm_price``) with a smile/skew and
  term-structure IV surface; greeks are ``bsm_greeks`` on the same IV, so
  ``implied_volatility(price) ~= iv`` (round-trip consistent). American puts/calls are floored
  at intrinsic value.
* Same inputs (incl. pinned ``as_of``) => same outputs (no RNG; volume/OI jitter comes from a CRC32 of the OCC symbol).
* Overridable spot / IV / time / dividend yield / market-open flag, plus scenario presets:
  ``gap_up``, ``gap_down``, ``crash``, ``iv_crush``, ``iv_spike``, ``expiry``, ``ex_div``.
* Non-100 multiplier support (``multiplier_overrides={"ADJ": 150}`` -> OCC root ``ADJ1``).
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from quantara_options.contracts import (
    DataFeedTier,
    ExerciseStyle,
    Greeks,
    OptionChainResponse,
    OptionContractSpec,
    OptionQuoteSnapshot,
    OptionRight,
)
from quantara_options.market_data.provider_base import (
    WARN_CONTRACT_EXPIRED,
    WARN_MARKET_CLOSED,
    WARN_NO_BID,
    WARN_NONSTANDARD_MULTIPLIER,
    WARN_SYNTHETIC_FIXTURE,
    build_occ_symbol,
    calendar_dte,
    closed_market_warnings,
    ensure_utc,
    expiry_datetime,
    is_nyse_market_open,
    is_valid_occ_symbol,
    nyse_session,
    parse_occ_symbol,
    spread_pct,
    to_ny,
    years_to_expiry,
)
from quantara_options.math_pricing import bsm_greeks, bsm_price

DEFAULT_SPOTS: dict[str, float] = {
    "SPY": 500.0, "QQQ": 430.0, "IWM": 205.0, "AAPL": 190.0, "MSFT": 420.0,
    "NVDA": 900.0, "TSLA": 250.0, "AMZN": 180.0, "META": 480.0, "GOOGL": 170.0,
}
DEFAULT_BASE_IV: dict[str, float] = {
    "SPY": 0.16, "QQQ": 0.20, "IWM": 0.22, "AAPL": 0.25, "MSFT": 0.24,
    "NVDA": 0.50, "TSLA": 0.55, "AMZN": 0.30, "META": 0.35, "GOOGL": 0.28,
}
DEFAULT_SPOT = 100.0
DEFAULT_IV = 0.30

SCENARIOS: dict[str, dict[str, float]] = {
    "gap_up": {"spot_shock_pct": 0.05, "iv_shift": -0.02},
    "gap_down": {"spot_shock_pct": -0.05, "iv_shift": 0.05},
    "crash": {"spot_shock_pct": -0.15, "iv_shift": 0.25},
    "iv_crush": {"iv_mult": 0.60},
    "iv_spike": {"iv_shift": 0.20},
}

# Handy pinned anchor for tests: a Tuesday during RTH (2025-06-03 15:00 UTC = 11:00 ET).
FIXTURE_DEFAULT_AS_OF = datetime(2025, 6, 3, 15, 0, tzinfo=timezone.utc)


def _round_cent(x: float) -> Decimal:
    return Decimal(str(round(x + 1e-12, 2))).quantize(Decimal("0.01"))


def strike_step(spot: float) -> float:
    if spot < 25:
        return 0.5
    if spot < 50:
        return 1.0
    if spot < 200:
        return 2.5
    if spot < 1000:
        return 5.0
    return 10.0


@dataclass
class _Overrides:
    spot: dict[str, float]
    iv: Optional[float] = None            # absolute flat IV (replaces surface base)
    iv_shift: float = 0.0                 # additive vol points
    iv_mult: float = 1.0
    spot_shock_pct: float = 0.0
    div_yield: float = 0.0
    ex_div_amount: float = 0.0
    as_of: Optional[datetime] = None
    market_open: Optional[bool] = None
    scenario: Optional[str] = None


class FixtureOptionsProvider:
    """Implements :class:`quantara_options.interfaces.OptionsDataProvider` with synthetic data."""

    provider_name = "fixture"

    def __init__(
        self,
        *,
        spots: dict[str, float] | None = None,
        base_iv: dict[str, float] | None = None,
        iv: float | None = None,
        as_of: datetime | None = None,
        market_open: bool | None = None,
        risk_free_rate: float = 0.045,
        dividend_yield: float = 0.0,
        multiplier_overrides: dict[str, int] | None = None,
        expiration_weekdays: tuple[int, ...] = (4,),  # Friday weeklies
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._spots = {k.upper(): float(v) for k, v in {**DEFAULT_SPOTS, **(spots or {})}.items()}
        self._base_iv = {k.upper(): float(v) for k, v in {**DEFAULT_BASE_IV, **(base_iv or {})}.items()}
        self._ov = _Overrides(spot={}, iv=iv, as_of=as_of, market_open=market_open, div_yield=dividend_yield)
        self.risk_free_rate = float(risk_free_rate)
        self._mult = {k.upper(): int(v) for k, v in (multiplier_overrides or {}).items()}
        self._weekdays = expiration_weekdays
        self._now_fn = now_fn
        self._calls = {"chains": 0, "quotes": 0, "underlying": 0}

    # ------------------------------------------------------------ overrides
    def set_overrides(
        self,
        *,
        spot: dict[str, float] | float | None = None,
        symbol: str | None = None,
        iv: float | None = None,
        iv_shift: float | None = None,
        iv_mult: float | None = None,
        spot_shock_pct: float | None = None,
        dividend_yield: float | None = None,
        as_of: datetime | None = None,
        market_open: bool | None = None,
        clear: bool = False,
    ) -> "FixtureOptionsProvider":
        if clear:
            self._ov = _Overrides(spot={})
        if spot is not None:
            if isinstance(spot, dict):
                self._ov.spot.update({k.upper(): float(v) for k, v in spot.items()})
            elif symbol:
                self._ov.spot[symbol.upper()] = float(spot)
            else:
                raise ValueError("scalar spot override requires symbol=")
        if iv is not None:
            self._ov.iv = float(iv)
        if iv_shift is not None:
            self._ov.iv_shift = float(iv_shift)
        if iv_mult is not None:
            self._ov.iv_mult = float(iv_mult)
        if spot_shock_pct is not None:
            self._ov.spot_shock_pct = float(spot_shock_pct)
        if dividend_yield is not None:
            self._ov.div_yield = float(dividend_yield)
        if as_of is not None:
            self._ov.as_of = ensure_utc(as_of)
        if market_open is not None:
            self._ov.market_open = market_open
        return self

    def apply_scenario(self, name: str, symbol: str | None = None, **kw: float) -> "FixtureOptionsProvider":
        """Apply a named stress scenario (``gap_up|gap_down|crash|iv_crush|iv_spike|expiry|ex_div``)."""
        key = name.strip().lower()
        if key in SCENARIOS:
            p = {**SCENARIOS[key], **kw}
            self.set_overrides(
                spot_shock_pct=p.get("spot_shock_pct", 0.0),
                iv_shift=p.get("iv_shift", 0.0),
                iv_mult=p.get("iv_mult", 1.0),
            )
        elif key == "expiry":
            base = self._ov.as_of or self._default_now()
            exp = self.next_expiration(base.astimezone(timezone.utc), min_dte=0)
            sess = nyse_session(exp)
            close = sess.close_dt or datetime.combine(exp, dtime(16, 0), tzinfo=to_ny().tzinfo)
            self.set_overrides(as_of=(close - timedelta(minutes=kw.get("minutes_before_close", 30))).astimezone(timezone.utc))
        elif key == "ex_div":
            amount = float(kw.get("dividend", 1.0))
            self._ov.ex_div_amount = amount
            self.set_overrides(dividend_yield=float(kw.get("dividend_yield", 0.02)))
        else:
            raise ValueError(f"unknown scenario: {name!r}")
        self._ov.scenario = key
        return self

    def reset(self) -> "FixtureOptionsProvider":
        self._ov = _Overrides(spot={})
        return self

    # --------------------------------------------------------------- state
    def _default_now(self) -> datetime:
        # Wall clock unless pinned via as_of / set_overrides(as_of=) / now_fn (tests should pin).
        return ensure_utc(self._now_fn()) if self._now_fn else datetime.now(timezone.utc)

    def _now(self, as_of: datetime | None = None) -> datetime:
        if as_of is not None:
            return ensure_utc(as_of)
        if self._ov.as_of is not None:
            return self._ov.as_of
        return self._default_now()

    def _is_open(self, now: datetime) -> bool:
        if self._ov.market_open is not None:
            return self._ov.market_open
        return is_nyse_market_open(now)

    def _spot(self, symbol: str) -> float:
        s = symbol.upper()
        base = self._ov.spot.get(s, self._spots.get(s, DEFAULT_SPOT))
        px = base * (1.0 + self._ov.spot_shock_pct) - self._ov.ex_div_amount
        return max(0.01, round(px, 4))

    def _atm_iv(self, symbol: str) -> float:
        base = self._ov.iv if self._ov.iv is not None else self._base_iv.get(symbol.upper(), DEFAULT_IV)
        return max(0.03, base * self._ov.iv_mult + self._ov.iv_shift)

    def _surface_iv(self, symbol: str, spot: float, strike: float, t_years: float) -> float:
        atm = self._atm_iv(symbol)
        m = math.log(strike / spot)
        dte = t_years * 365.0
        term = 1.0 + 0.12 * math.exp(-dte / 25.0)       # short-dated vol premium
        skew = -0.30 * m + 1.2 * m * m                   # put skew + smile
        return min(3.0, max(0.05, (atm + atm * skew) * term))

    def _multiplier_for(self, root: str) -> int:
        r = root.upper()
        base = r[:-1] if r and r[-1].isdigit() else r
        return self._mult.get(r, self._mult.get(base, 100))

    def _root_for(self, symbol: str) -> str:
        s = symbol.upper()
        return f"{s}1" if (s in self._mult and self._mult[s] != 100) else s

    # --------------------------------------------------------- expirations
    def expirations(self, as_of: datetime, min_dte: int, max_dte: int) -> list[date]:
        today = to_ny(as_of).date()
        out: list[date] = []
        d = today + timedelta(days=max(0, min_dte))
        end = today + timedelta(days=max_dte)
        while d <= end:
            if d.weekday() in self._weekdays and nyse_session(d).is_trading_day:
                out.append(d)
            elif d.weekday() in self._weekdays:  # holiday Friday -> prior trading day (e.g. Good Friday)
                prev = d - timedelta(days=1)
                while not nyse_session(prev).is_trading_day:
                    prev -= timedelta(days=1)
                if today + timedelta(days=max(0, min_dte)) <= prev <= end and prev not in out:
                    out.append(prev)
            d += timedelta(days=1)
        # monthly 3rd-Friday style expirations are already Fridays; ensure sorted/unique
        return sorted(set(out))

    def next_expiration(self, as_of: datetime, min_dte: int = 0) -> date:
        exps = self.expirations(as_of, min_dte, min_dte + 14)
        if not exps:
            raise RuntimeError("no fixture expiration found")  # pragma: no cover
        return exps[0]

    # ------------------------------------------------------------- pricing
    def _quote_for(
        self,
        *,
        spec: OptionContractSpec,
        spot: float,
        now: datetime,
        market_open: bool,
        extra_warnings: list[str] | None = None,
    ) -> OptionQuoteSnapshot:
        warns = [WARN_SYNTHETIC_FIXTURE, *(extra_warnings or [])]
        if not market_open:
            warns.append(WARN_MARKET_CLOSED)
        if spec.multiplier != 100:
            warns.append(f"{WARN_NONSTANDARD_MULTIPLIER}:{spec.multiplier}")
        k = float(spec.strike)
        t = years_to_expiry(spec.expiration_date, now)
        expired = t <= 0.0
        q = self._ov.div_yield
        intrinsic = max(0.0, spot - k) if spec.right == OptionRight.CALL else max(0.0, k - spot)

        iv: Optional[float]
        if expired:
            warns.append(WARN_CONTRACT_EXPIRED)
            iv = None
            theo = intrinsic
            greeks = Greeks(delta=None, gamma=None, theta=None, vega=None, rho=None, computed_locally=False)
        else:
            iv = round(self._surface_iv(spec.underlying_symbol, spot, k, t), 4)
            theo = max(
                intrinsic,
                bsm_price(spot=spot, strike=k, t_years=t, rate=self.risk_free_rate, sigma=iv, right=spec.right, div_yield=q),
            )
            greeks = bsm_greeks(
                spot=spot, strike=k, t_years=t, rate=self.risk_free_rate, sigma=iv,
                right=spec.right, div_yield=q, computed_locally=False,
            )
            greeks.computed_locally = False

        # Realistic spread: wider for OTM / cheap / long-dated; tick 0.01.
        moneyness = abs(math.log(k / spot)) if spot > 0 and k > 0 else 0.0
        dte = max(0.0, t * 365.0)
        rel = 0.018 + 0.55 * moneyness + (0.015 if dte < 2 else 0.0) + (0.01 if dte > 45 else 0.0)
        spread = max(0.01, theo * rel, 0.02 if theo >= 1.0 else 0.01)
        spread = round(spread, 2)
        if expired or theo <= 0.0:
            bid_f, ask_f = (max(0.0, theo), max(0.0, theo)) if expired else (0.0, 0.01)
        else:
            bid_f = max(0.0, theo - spread / 2.0)
            ask_f = max(bid_f + 0.01, theo + spread / 2.0)
        bid, ask = _round_cent(bid_f), _round_cent(ask_f)
        if ask < bid:
            ask = bid
        mid = ((bid + ask) / 2).quantize(Decimal("0.0001"))
        if bid == 0:
            warns.append(WARN_NO_BID)

        # Deterministic liquidity profile
        h = zlib.crc32(spec.occ_symbol.encode("ascii", "ignore")) % 1000 / 1000.0
        atm_w = math.exp(-((moneyness / 0.06) ** 2))
        dte_w = 1.0 / (1.0 + dte / 20.0)
        liquid_base = 40000.0 if spec.underlying_symbol in ("SPY", "QQQ") else 6000.0
        volume = 0 if expired else int(liquid_base * atm_w * (0.4 + 0.6 * dte_w) * (0.7 + 0.6 * h))
        oi = 0 if expired else int(liquid_base * 4.0 * (0.15 + 0.85 * atm_w) * (0.7 + 0.6 * (1.0 - h)) * (0.5 + 0.5 * math.sqrt(dte_w)))

        return OptionQuoteSnapshot(
            contract=spec.model_copy(update={"open_interest": oi, "close_price": _round_cent(theo)}),
            underlying_price=Decimal(str(round(spot, 4))),
            bid=bid,
            ask=ask,
            mid=mid,
            last_trade=_round_cent(theo),
            volume=volume,
            open_interest=oi,
            implied_volatility=iv,
            greeks=greeks,
            quote_timestamp=now,
            snapshot_timestamp=now,
            feed_tier=DataFeedTier.FIXTURE,
            is_indicative=False,
            is_delayed_trades=False,
            is_stale=False,
            spread_pct=spread_pct(bid, ask),
            data_warnings=list(dict.fromkeys(warns)),
        )

    def _spec(self, symbol: str, exp: date, right: OptionRight, strike: Decimal) -> OptionContractSpec:
        root = self._root_for(symbol)
        return OptionContractSpec(
            occ_symbol=build_occ_symbol(root, exp, right, strike),
            underlying_symbol=symbol,
            right=right,
            strike=strike,
            expiration_date=exp,
            multiplier=self._multiplier_for(root),
            exercise_style=ExerciseStyle.AMERICAN,
            tradable=True,
            root_symbol=root,
        )

    # ------------------------------------------------------ provider protocol
    def get_underlying_price(self, symbol: str) -> tuple[Decimal | None, bool, list[str]]:
        self._calls["underlying"] += 1
        now = self._now()
        market_open = self._is_open(now)
        warns = [WARN_SYNTHETIC_FIXTURE, *closed_market_warnings(market_open, now)]
        if self._ov.scenario:
            warns.append(f"scenario:{self._ov.scenario}")
        return Decimal(str(self._spot(symbol.upper()))), market_open, warns

    def get_option_chain(
        self,
        symbol: str,
        *,
        min_dte: int = 3,
        max_dte: int = 60,
        strike_range_pct: float = 0.20,
        as_of: datetime | None = None,
    ) -> OptionChainResponse:
        self._calls["chains"] += 1
        sym = symbol.strip().upper()
        now = self._now(as_of)
        market_open = self._is_open(now)
        spot = self._spot(sym)
        step = strike_step(spot)
        lo_k = spot * (1.0 - strike_range_pct)
        hi_k = spot * (1.0 + strike_range_pct)
        first = math.ceil(lo_k / step) * step
        strikes: list[Decimal] = []
        k = first
        while k <= hi_k + 1e-9:
            strikes.append(Decimal(str(round(k, 2))))
            k += step

        quotes: list[OptionQuoteSnapshot] = []
        for exp in self.expirations(now, min_dte, max_dte):
            for strike in strikes:
                for right in (OptionRight.CALL, OptionRight.PUT):
                    spec = self._spec(sym, exp, right, strike)
                    quotes.append(self._quote_for(spec=spec, spot=spot, now=now, market_open=market_open))

        warnings = [WARN_SYNTHETIC_FIXTURE, *closed_market_warnings(market_open, now)]
        if self._ov.scenario:
            warnings.append(f"scenario:{self._ov.scenario}")
        if not quotes:
            warnings.append("no_contracts_in_filter")
        return OptionChainResponse(
            underlying_symbol=sym,
            underlying_price=Decimal(str(round(spot, 4))),
            as_of=now,
            market_open=market_open,
            feed_tier=DataFeedTier.FIXTURE,
            is_indicative=False,
            is_delayed_trades=False,
            is_stale=False,
            expirations=sorted({q.contract.expiration_date for q in quotes}),
            contracts=quotes,
            warnings=list(dict.fromkeys(warnings)),
            provider_name=self.provider_name,
        )

    def get_quotes_for_occ(
        self,
        occ_symbols: list[str],
        *,
        underlying_hint: str | None = None,
    ) -> dict[str, OptionQuoteSnapshot]:
        self._calls["quotes"] += 1
        now = self._now()
        market_open = self._is_open(now)
        out: dict[str, OptionQuoteSnapshot] = {}
        for raw in occ_symbols or []:
            occ = str(raw).strip().upper().replace(" ", "")
            if not is_valid_occ_symbol(occ):
                continue
            p = parse_occ_symbol(occ)
            und = (underlying_hint or p.underlying_symbol).upper()
            spec = OptionContractSpec(
                occ_symbol=occ,
                underlying_symbol=und,
                right=p.right,
                strike=p.strike,
                expiration_date=p.expiration_date,
                multiplier=self._multiplier_for(p.root_symbol),
                exercise_style=ExerciseStyle.AMERICAN,
                root_symbol=p.root_symbol,
            )
            out[occ] = self._quote_for(spec=spec, spot=self._spot(und), now=now, market_open=market_open)
        return out

    def get_provider_health(self) -> dict:
        now = self._now()
        return {
            "provider": self.provider_name,
            "ok": True,
            "credentials_configured": False,
            "feed_tier": DataFeedTier.FIXTURE.value,
            "is_indicative": False,
            "is_delayed_trades": False,
            "market_open": self._is_open(now),
            "as_of": now.isoformat(),
            "scenario": self._ov.scenario,
            "rate_limit": {"enabled": False},
            "cache": {"hits": 0, "misses": 0, "hit_ratio": None},
            "calls": dict(self._calls),
            "last_error": None,
            "warnings": [WARN_SYNTHETIC_FIXTURE],
        }
