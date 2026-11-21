"""Alpaca read-only options market-data provider for QUANTARA Options.

SAFETY
------
* Read-only: only HTTP ``GET`` against an allow-list of market-data / contract-metadata
  paths. Trading / order / account endpoints are structurally unreachable
  (``_assert_read_only_path``).
* API credentials are never logged, printed, returned in health output or put in exceptions.

DATA TIER
---------
Alpaca *Basic* returns **indicative** option quotes and **15-minute delayed** trades.
Every chain/quote is therefore labelled ``DataFeedTier.ALPACA_INDICATIVE`` /
``is_indicative=True`` / ``is_delayed_trades=True`` unless the provider is explicitly
configured with ``feed="opra"`` AND the OPRA request actually succeeded (HTTP 200).
If OPRA is requested but denied (401/403/422) the provider falls back to the indicative
feed and says so in the warnings.

GREEKS / IV
-----------
When Alpaca omits greeks or ``impliedVolatility`` (common off-hours / indicative), they are
computed locally from underlying spot + mid (fallback: last trade, then prior close) via
``quantara_options.math_pricing`` and flagged ``greeks.computed_locally=True``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

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
    WARN_CREDENTIALS_MISSING,
    WARN_CROSSED_QUOTE,
    WARN_DELAYED_TRADES,
    WARN_INDICATIVE_FEED,
    WARN_IV_FROM_CLOSE,
    WARN_IV_FROM_LAST,
    WARN_IV_UNAVAILABLE,
    WARN_LOCAL_GREEKS,
    WARN_LOCAL_IV,
    WARN_MARKET_CLOSED,
    WARN_MISSING_QUOTE,
    WARN_NO_ASK,
    WARN_NO_BID,
    WARN_NONSTANDARD_MULTIPLIER,
    WARN_STALE_QUOTE,
    WARN_UNDERLYING_UNAVAILABLE,
    WARN_WIDE_SPREAD,
    WIDE_SPREAD_PCT,
    TTLCache,
    TokenBucket,
    calendar_dte,
    closed_market_warnings,
    ensure_utc,
    is_nyse_market_open,
    is_valid_occ_symbol,
    parse_iso_timestamp,
    parse_occ_symbol,
    spread_pct,
    to_decimal,
    to_float,
    to_int,
    to_ny,
    years_to_expiry,
)

try:  # math_pricing is owned by another module; keep import errors explicit
    from quantara_options.math_pricing import bsm_greeks, implied_volatility
except Exception:  # pragma: no cover
    bsm_greeks = None  # type: ignore[assignment]
    implied_volatility = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# (url, headers, timeout_seconds) -> (http_status, parsed_json_or_None, response_headers)
Transport = Callable[[str, dict[str, str], float], tuple[int, Any, dict[str, str]]]

_ALLOWED_PATH_PREFIXES = (
    "/v2/options/contracts",
    "/v1beta1/options/snapshots",
    "/v2/stocks/",
)
_FORBIDDEN_PATH_FRAGMENTS = ("/orders", "/positions", "/account", "/watchlists", "/activities")


class AlpacaHTTPError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"alpaca http {status}: {message[:200]}")
        self.status = status
        self.message = message


class AlpacaRateLimitTimeout(Exception):
    pass


def _default_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {}), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"message": str(exc.reason)}
        return exc.code, body, dict(exc.headers.items()) if exc.headers else {}


def _assert_read_only_path(path: str) -> None:
    if not path.startswith(_ALLOWED_PATH_PREFIXES) or any(f in path for f in _FORBIDDEN_PATH_FRAGMENTS):
        raise ValueError(f"path not allowed for read-only options market data: {path}")


def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s or len(s) > 12 or not all(ch.isalnum() or ch in ".-" for ch in s):
        raise ValueError(f"invalid underlying symbol: {symbol!r}")
    return s


class AlpacaOptionsProvider:
    """Implements :class:`quantara_options.interfaces.OptionsDataProvider` on Alpaca."""

    provider_name = "alpaca"

    def __init__(
        self,
        *,
        api_key_id: str | None = None,
        api_secret_key: str | None = None,
        paper_base_url: str | None = None,
        data_base_url: str | None = None,
        feed: str = "indicative",
        stock_feed: str | None = None,
        risk_free_rate: float = 0.045,
        dividend_yield: float | dict[str, float] = 0.0,
        transport: Transport | None = None,
        now_fn: Callable[[], datetime] | None = None,
        rate_limiter: TokenBucket | None = None,
        timeout_seconds: float = 15.0,
        stale_quote_seconds: float = 1800.0,
        max_contract_pages: int = 10,
        max_snapshot_pages: int = 10,
        page_size: int = 1000,
        max_local_iv_solves: int = 400,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if api_key_id is None or api_secret_key is None or paper_base_url is None or data_base_url is None or stock_feed is None:
            from quantara_engine.core.config import settings  # read-only settings access

            api_key_id = settings.alpaca_api_key_id if api_key_id is None else api_key_id
            api_secret_key = settings.alpaca_api_secret_key if api_secret_key is None else api_secret_key
            paper_base_url = settings.alpaca_paper_base_url if paper_base_url is None else paper_base_url
            data_base_url = settings.alpaca_data_base_url if data_base_url is None else data_base_url
            stock_feed = settings.alpaca_data_feed if stock_feed is None else stock_feed
        self._key_id = (api_key_id or "").strip()
        self._secret = (api_secret_key or "").strip()
        self._paper_base = (paper_base_url or "https://paper-api.alpaca.markets").rstrip("/")
        self._data_base = (data_base_url or "https://data.alpaca.markets").rstrip("/")
        self.feed = (feed or "indicative").strip().lower()
        if self.feed not in ("indicative", "opra"):
            raise ValueError("feed must be 'indicative' or 'opra'")
        self.stock_feed = (stock_feed or "iex").strip().lower() or "iex"
        self.risk_free_rate = float(risk_free_rate)
        self._div = dividend_yield
        self._transport: Transport = transport or _default_transport
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._timeout = float(timeout_seconds)
        self.stale_quote_seconds = float(stale_quote_seconds)
        self.max_contract_pages = int(max_contract_pages)
        self.max_snapshot_pages = int(max_snapshot_pages)
        self.page_size = max(1, min(int(page_size), 1000))
        self.max_local_iv_solves = int(max_local_iv_solves)
        self.max_retries = int(max_retries)
        self._sleep = sleep
        self._limiter = rate_limiter or TokenBucket(rate_per_sec=3.0, capacity=10)

        self._stock_cache = TTLCache(5.0, max_entries=256)
        self._contract_cache = TTLCache(300.0, max_entries=64)
        self._snapshot_cache = TTLCache(10.0, max_entries=64)
        self._spec_cache = TTLCache(3600.0, max_entries=20000)
        self._quote_cache = TTLCache(3.0, max_entries=128)

        self._opra_denied = False
        self._opra_confirmed = False
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[datetime] = None
        self._last_success_at: Optional[datetime] = None
        self._http = {"requests": 0, "ok": 0, "errors": 0, "http_429": 0, "retries": 0}

    # ------------------------------------------------------------------ helpers
    @property
    def has_credentials(self) -> bool:
        return bool(self._key_id and self._secret)

    def _now(self) -> datetime:
        return ensure_utc(self._now_fn())

    def _div_yield(self, symbol: str) -> float:
        if isinstance(self._div, dict):
            return float(self._div.get(symbol.upper(), 0.0))
        return float(self._div or 0.0)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        }

    def _record_error(self, msg: str) -> None:
        self._last_error = msg[:300]
        self._last_error_at = self._now()

    def _get(self, base: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """Rate-limited GET with bounded retries on 429/5xx/network errors."""
        _assert_read_only_path(path)
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = base + path + (("?" + urllib.parse.urlencode(clean)) if clean else "")
        attempt = 0
        while True:
            if not self._limiter.acquire(timeout=self._timeout):
                self._record_error("client_rate_limit_timeout")
                raise AlpacaRateLimitTimeout("client-side rate limiter timeout")
            self._http["requests"] += 1
            try:
                status, body, resp_headers = self._transport(url, self._headers(), self._timeout)
            except Exception as exc:  # network error
                status, body, resp_headers = 0, {"message": type(exc).__name__}, {}
            if 200 <= status < 300:
                self._http["ok"] += 1
                self._last_success_at = self._now()
                return body if body is not None else {}
            self._http["errors"] += 1
            retryable = status in (0, 429, 500, 502, 503, 504)
            if status == 429:
                self._http["http_429"] += 1
            msg = ""
            if isinstance(body, dict):
                msg = str(body.get("message", ""))
            if retryable and attempt < self.max_retries:
                attempt += 1
                self._http["retries"] += 1
                wait = 0.5 * (2 ** (attempt - 1))
                ra = to_float((resp_headers or {}).get("Retry-After") or (resp_headers or {}).get("retry-after"))
                if ra is not None:
                    wait = min(max(ra, 0.0), 5.0)
                self._sleep(min(wait, 5.0))
                continue
            err = AlpacaHTTPError(status, msg)
            self._record_error(f"{path.split('?')[0]} -> {status}")
            raise err

    # ------------------------------------------------------------ underlying
    def _stock_snapshot(self, symbol: str) -> dict[str, Any]:
        cached = self._stock_cache.get(symbol)
        if cached is not None:
            return cached
        path = f"/v2/stocks/{urllib.parse.quote(symbol)}/snapshot"
        try:
            data = self._get(self._data_base, path, {"feed": self.stock_feed})
        except AlpacaHTTPError as exc:
            if exc.status in (403, 422, 400):  # feed not permitted on this plan -> default feed
                data = self._get(self._data_base, path, {})
            else:
                raise
        if not isinstance(data, dict):
            data = {}
        self._stock_cache.set(symbol, data)
        return data

    @staticmethod
    def _pick_spot(snap: dict[str, Any]) -> tuple[Optional[Decimal], Optional[datetime], str]:
        lt = snap.get("latestTrade") or {}
        p = to_decimal(lt.get("p"))
        if p is not None and p > 0:
            return p, parse_iso_timestamp(lt.get("t")), "latest_trade"
        lq = snap.get("latestQuote") or {}
        bp, ap = to_decimal(lq.get("bp")), to_decimal(lq.get("ap"))
        if bp is not None and ap is not None and bp > 0 and ap >= bp:
            return (bp + ap) / 2, parse_iso_timestamp(lq.get("t")), "quote_mid"
        for key, label in (("minuteBar", "minute_bar"), ("dailyBar", "daily_bar"), ("prevDailyBar", "prev_daily_bar")):
            bar = snap.get(key) or {}
            c = to_decimal(bar.get("c"))
            if c is not None and c > 0:
                return c, parse_iso_timestamp(bar.get("t")), label
        return None, None, "none"

    def get_underlying_price(self, symbol: str) -> tuple[Decimal | None, bool, list[str]]:
        warnings: list[str] = []
        now = self._now()
        market_open = is_nyse_market_open(now)
        warnings.extend(closed_market_warnings(market_open, now))
        try:
            sym = _norm_symbol(symbol)
        except ValueError:
            return None, market_open, [*warnings, WARN_UNDERLYING_UNAVAILABLE, "invalid_symbol"]
        if not self.has_credentials:
            return None, market_open, [*warnings, WARN_CREDENTIALS_MISSING, WARN_UNDERLYING_UNAVAILABLE]
        try:
            snap = self._stock_snapshot(sym)
        except (AlpacaHTTPError, AlpacaRateLimitTimeout) as exc:
            status = getattr(exc, "status", "rate_limit")
            return None, market_open, [*warnings, WARN_UNDERLYING_UNAVAILABLE, f"underlying_http_{status}"]
        price, ts, source = self._pick_spot(snap)
        if price is None:
            return None, market_open, [*warnings, WARN_UNDERLYING_UNAVAILABLE]
        if source != "latest_trade":
            warnings.append(f"underlying_price_source:{source}")
        if market_open and ts is not None and (now - ts).total_seconds() > 1200:
            warnings.append("underlying_price_stale")
        if self.stock_feed == "iex":
            warnings.append("underlying_feed_iex")
        return price, market_open, warnings

    # -------------------------------------------------------------- contracts
    @staticmethod
    def _contract_to_spec(c: dict[str, Any]) -> Optional[OptionContractSpec]:
        occ = str(c.get("symbol") or "").strip().upper()
        if not is_valid_occ_symbol(occ):
            return None
        parsed = parse_occ_symbol(occ)
        strike = to_decimal(c.get("strike_price")) or parsed.strike
        exp_raw = c.get("expiration_date")
        try:
            exp = date.fromisoformat(str(exp_raw)) if exp_raw else parsed.expiration_date
        except ValueError:
            exp = parsed.expiration_date
        t = str(c.get("type") or "").lower()
        right = OptionRight.CALL if t == "call" else OptionRight.PUT if t == "put" else parsed.right
        style_raw = str(c.get("style") or "american").lower()
        style = ExerciseStyle.EUROPEAN if style_raw == "european" else ExerciseStyle.AMERICAN
        # multiplier: prefer explicit 'multiplier', then contract 'size'; never assume 100 if given.
        mult = to_int(c.get("multiplier"), 0) or to_int(c.get("size"), 0) or 100
        return OptionContractSpec(
            occ_symbol=occ,
            underlying_symbol=str(c.get("underlying_symbol") or parsed.underlying_symbol).upper(),
            right=right,
            strike=strike,
            expiration_date=exp,
            multiplier=mult,
            exercise_style=style,
            tradable=bool(c.get("tradable", True)),
            root_symbol=c.get("root_symbol") or parsed.root_symbol,
            open_interest=to_int(c.get("open_interest"), 0),
            close_price=to_decimal(c.get("close_price")),
        )

    def _fetch_contracts(self, params: dict[str, Any]) -> tuple[list[OptionContractSpec], bool]:
        """Paginated (bounded) GET /v2/options/contracts. Returns (specs, truncated)."""
        key = tuple(sorted(params.items()))
        cached = self._contract_cache.get(key)
        if cached is not None:
            return cached
        specs: list[OptionContractSpec] = []
        token: Optional[str] = None
        truncated = False
        for page in range(self.max_contract_pages):
            q = {**params, "limit": self.page_size, "status": "active", "page_token": token}
            data = self._get(self._paper_base, "/v2/options/contracts", q)
            for raw in (data.get("option_contracts") or []) if isinstance(data, dict) else []:
                spec = self._contract_to_spec(raw)
                if spec is not None:
                    specs.append(spec)
            token = data.get("next_page_token") if isinstance(data, dict) else None
            if not token:
                break
            if page == self.max_contract_pages - 1:
                truncated = True
        for s in specs:
            self._spec_cache.set(s.occ_symbol, s)
        result = (specs, truncated)
        self._contract_cache.set(key, result)
        return result

    def _lookup_spec(self, occ: str) -> tuple[Optional[OptionContractSpec], list[str]]:
        spec = self._spec_cache.get(occ)
        if spec is not None:
            return spec, []
        try:
            data = self._get(self._paper_base, f"/v2/options/contracts/{urllib.parse.quote(occ)}")
            spec = self._contract_to_spec(data) if isinstance(data, dict) else None
            if spec is not None:
                self._spec_cache.set(occ, spec)
                return spec, []
        except (AlpacaHTTPError, AlpacaRateLimitTimeout):
            pass
        parsed = parse_occ_symbol(occ)
        fallback = OptionContractSpec(
            occ_symbol=occ,
            underlying_symbol=parsed.underlying_symbol,
            right=parsed.right,
            strike=parsed.strike,
            expiration_date=parsed.expiration_date,
            multiplier=100,
            root_symbol=parsed.root_symbol,
        )
        warns = ["contract_metadata_unavailable_default_multiplier"]
        if parsed.is_adjusted_root:
            warns.append("adjusted_contract_multiplier_unverified")
        return fallback, warns

    # -------------------------------------------------------------- snapshots
    def _fetch_snapshots(
        self, path: str, params: dict[str, Any], max_pages: int
    ) -> tuple[dict[str, Any], bool, str, list[str]]:
        """Fetch snapshots map (paginated, bounded). Returns (map, truncated, effective_feed, warnings)."""
        warnings: list[str] = []
        feed = "opra" if (self.feed == "opra" and not self._opra_denied) else "indicative"
        snaps: dict[str, Any] = {}
        truncated = False
        token: Optional[str] = None
        for page in range(max_pages):
            q = {**params, "feed": feed, "page_token": token}
            try:
                data = self._get(self._data_base, path, q)
            except AlpacaHTTPError as exc:
                if feed == "opra" and exc.status in (401, 403, 422):
                    self._opra_denied = True
                    feed = "indicative"
                    warnings.append("opra_unavailable_fell_back_to_indicative")
                    data = self._get(self._data_base, path, {**params, "feed": feed, "page_token": token})
                else:
                    raise
            if feed == "opra":
                self._opra_confirmed = True
            m = data.get("snapshots") if isinstance(data, dict) else None
            if isinstance(m, dict):
                snaps.update(m)
            token = data.get("next_page_token") if isinstance(data, dict) else None
            if not token:
                break
            if page == max_pages - 1:
                truncated = True
        return snaps, truncated, feed, warnings

    def _tier_flags(self, effective_feed: str) -> tuple[DataFeedTier, bool, bool]:
        if effective_feed == "opra" and self._opra_confirmed:
            return DataFeedTier.ALPACA_OPRA, False, False
        return DataFeedTier.ALPACA_INDICATIVE, True, True

    # ------------------------------------------------------------ quote build
    def _build_quote(
        self,
        spec: OptionContractSpec,
        snap: dict[str, Any] | None,
        spot: Decimal | None,
        *,
        now: datetime,
        market_open: bool,
        tier: DataFeedTier,
        indicative: bool,
        delayed: bool,
        solve_budget: dict[str, int],
        extra_warnings: list[str] | None = None,
    ) -> OptionQuoteSnapshot:
        warns: list[str] = list(extra_warnings or [])
        snap = snap or {}
        if not market_open:
            warns.append(WARN_MARKET_CLOSED)
        if indicative:
            warns.append(WARN_INDICATIVE_FEED)
        if spec.multiplier != 100:
            warns.append(f"{WARN_NONSTANDARD_MULTIPLIER}:{spec.multiplier}")

        lq = snap.get("latestQuote") or {}
        lt = snap.get("latestTrade") or {}
        raw_b, raw_a = to_decimal(lq.get("bp")), to_decimal(lq.get("ap"))
        bid: Optional[Decimal] = None
        ask: Optional[Decimal] = None
        mid: Optional[Decimal] = None
        if raw_a is None or raw_a <= 0:
            if raw_b is not None and raw_b > 0:
                bid = raw_b
                warns.append(WARN_NO_ASK)
            warns.append(WARN_MISSING_QUOTE)
        elif raw_b is None:
            ask = raw_a
            warns.append(WARN_NO_BID)
            warns.append(WARN_MISSING_QUOTE)
        elif raw_a < raw_b:
            bid, ask = raw_b, raw_a
            warns.append(WARN_CROSSED_QUOTE)
        else:
            bid, ask = raw_b, raw_a
            mid = ((bid + ask) / 2).quantize(Decimal("0.0001"))
            if bid == 0:
                warns.append(WARN_NO_BID)
        sp = spread_pct(bid, ask)
        if sp is not None and sp > WIDE_SPREAD_PCT:
            warns.append(WARN_WIDE_SPREAD)

        last_trade = to_decimal(lt.get("p"))
        if last_trade is not None and last_trade <= 0:
            last_trade = None
        quote_ts = parse_iso_timestamp(lq.get("t"))
        stale = (not market_open) or quote_ts is None or (now - quote_ts).total_seconds() > self.stale_quote_seconds
        if market_open and quote_ts is not None and stale:
            warns.append(WARN_STALE_QUOTE)

        daily = snap.get("dailyBar") or {}
        volume = to_int(daily.get("v"), 0)

        # ---- IV & Greeks (provider first, local fallback) ----
        iv = to_float(snap.get("impliedVolatility"))
        if iv is not None and iv <= 0:
            iv = None
        g_raw = snap.get("greeks") or {}
        d, ga, th, ve = (to_float(g_raw.get(k)) for k in ("delta", "gamma", "theta", "vega"))
        provider_greeks_complete = None not in (d, ga, th, ve)
        greeks = Greeks()
        if provider_greeks_complete:
            greeks = Greeks(delta=d, gamma=ga, theta=th, vega=ve, rho=to_float(g_raw.get("rho")), computed_locally=False)

        expired = spec.expiration_date < to_ny(now).date()
        t_years = years_to_expiry(spec.expiration_date, now)
        spot_f = float(spot) if spot is not None else 0.0
        strike_f = float(spec.strike)
        need_iv = iv is None
        need_greeks = not provider_greeks_complete
        if expired:
            warns.append(WARN_CONTRACT_EXPIRED)
        elif (need_iv or need_greeks) and spot_f > 0 and t_years > 0 and bsm_greeks is not None:
            if need_iv:
                ref: Optional[float] = None
                if bid is not None and bid > 0 and mid is not None:
                    ref = float(mid)
                elif last_trade is not None:
                    ref = float(last_trade)
                    warns.append(WARN_IV_FROM_LAST)
                elif spec.close_price is not None and spec.close_price > 0:
                    ref = float(spec.close_price)
                    warns.append(WARN_IV_FROM_CLOSE)
                if ref is not None and solve_budget["left"] > 0:
                    solve_budget["left"] -= 1
                    iv = implied_volatility(
                        market_price=ref,
                        spot=spot_f,
                        strike=strike_f,
                        t_years=t_years,
                        rate=self.risk_free_rate,
                        right=spec.right,
                        div_yield=self._div_yield(spec.underlying_symbol),
                        exercise_style=spec.exercise_style,
                    )
                    if iv is not None:
                        warns.append(WARN_LOCAL_IV)
                elif ref is not None:
                    warns.append("local_iv_budget_exceeded")
                if iv is None:
                    warns.append(WARN_IV_UNAVAILABLE)
            if need_greeks and iv is not None:
                greeks = bsm_greeks(
                    spot=spot_f,
                    strike=strike_f,
                    t_years=t_years,
                    rate=self.risk_free_rate,
                    sigma=iv,
                    right=spec.right,
                    div_yield=self._div_yield(spec.underlying_symbol),
                    computed_locally=True,
                )
                greeks.computed_locally = True
                warns.append(WARN_LOCAL_GREEKS)
        elif need_iv and not expired:
            warns.append(WARN_IV_UNAVAILABLE)

        return OptionQuoteSnapshot(
            contract=spec,
            underlying_price=spot if spot is not None else Decimal("0"),
            bid=bid,
            ask=ask,
            mid=mid,
            last_trade=last_trade,
            volume=volume,
            open_interest=spec.open_interest,
            implied_volatility=iv,
            greeks=greeks,
            quote_timestamp=quote_ts,
            snapshot_timestamp=now,
            feed_tier=tier,
            is_indicative=indicative,
            is_delayed_trades=delayed,
            is_stale=stale,
            spread_pct=sp,
            data_warnings=list(dict.fromkeys(warns)),
        )

    # ------------------------------------------------------------------ chain
    def _empty_chain(self, symbol: str, now: datetime, market_open: bool, warnings: list[str],
                     tier: DataFeedTier = DataFeedTier.UNAVAILABLE) -> OptionChainResponse:
        return OptionChainResponse(
            underlying_symbol=symbol,
            underlying_price=None,
            as_of=now,
            market_open=market_open,
            feed_tier=tier,
            is_indicative=True,
            is_delayed_trades=True,
            is_stale=True,
            expirations=[],
            contracts=[],
            warnings=list(dict.fromkeys(warnings)),
            provider_name=self.provider_name,
        )

    def get_option_chain(
        self,
        symbol: str,
        *,
        min_dte: int = 3,
        max_dte: int = 60,
        strike_range_pct: float = 0.20,
        as_of: datetime | None = None,
    ) -> OptionChainResponse:
        now = ensure_utc(as_of) if as_of is not None else self._now()
        market_open = is_nyse_market_open(now)
        try:
            sym = _norm_symbol(symbol)
        except ValueError:
            return self._empty_chain(str(symbol).upper(), now, market_open, ["invalid_symbol"])
        if not self.has_credentials:
            return self._empty_chain(sym, now, market_open, [WARN_CREDENTIALS_MISSING, *closed_market_warnings(market_open, now)])

        spot, _live_open, spot_warns = self.get_underlying_price(sym)
        chain_warns: list[str] = [w for w in spot_warns if w != "market_closed" and not w.startswith("market_status:")]
        chain_warns.extend(closed_market_warnings(market_open, now))
        if spot is None:
            return self._empty_chain(sym, now, market_open, chain_warns, DataFeedTier.UNAVAILABLE)

        lo = (spot * Decimal(str(1.0 - strike_range_pct))).quantize(Decimal("0.01"))
        hi = (spot * Decimal(str(1.0 + strike_range_pct))).quantize(Decimal("0.01"))
        today = to_ny(now).date()
        d_from = today + timedelta(days=max(0, int(min_dte)))
        d_to = today + timedelta(days=max(0, int(max_dte)))
        filters = {
            "strike_price_gte": f"{lo}",
            "strike_price_lte": f"{hi}",
            "expiration_date_gte": d_from.isoformat(),
            "expiration_date_lte": d_to.isoformat(),
        }

        # 1) contract metadata
        specs: list[OptionContractSpec] = []
        metadata_ok = True
        try:
            specs, truncated = self._fetch_contracts({"underlying_symbols": sym, **filters})
            if truncated:
                chain_warns.append("contracts_truncated_max_pages")
        except (AlpacaHTTPError, AlpacaRateLimitTimeout) as exc:
            metadata_ok = False
            chain_warns.append(f"contract_metadata_unavailable:{getattr(exc, 'status', 'rate_limit')}")

        # 2) quote snapshots
        snaps_key = (sym, tuple(sorted(filters.items())), self.feed)
        cached = self._snapshot_cache.get(snaps_key)
        try:
            if cached is not None:
                snaps, snap_trunc, eff_feed, snap_warns = cached
            else:
                snaps, snap_trunc, eff_feed, snap_warns = self._fetch_snapshots(
                    f"/v1beta1/options/snapshots/{urllib.parse.quote(sym)}",
                    {**filters, "limit": self.page_size},
                    self.max_snapshot_pages,
                )
                self._snapshot_cache.set(snaps_key, (snaps, snap_trunc, eff_feed, snap_warns))
        except (AlpacaHTTPError, AlpacaRateLimitTimeout) as exc:
            snaps, snap_trunc, eff_feed, snap_warns = {}, False, "indicative", [f"snapshots_unavailable:{getattr(exc, 'status', 'rate_limit')}"]
        chain_warns.extend(snap_warns)
        if snap_trunc:
            chain_warns.append("snapshots_truncated_max_pages")

        tier, indicative, delayed = self._tier_flags(eff_feed)
        if indicative:
            chain_warns.append(WARN_INDICATIVE_FEED)
        if delayed:
            chain_warns.append(WARN_DELAYED_TRADES)

        # fallback when contract metadata is unavailable: derive specs from OCC symbols
        meta_default = False
        if not specs and snaps:
            for occ in snaps:
                if not is_valid_occ_symbol(occ):
                    continue
                p = parse_occ_symbol(occ)
                if p.underlying_symbol != sym:
                    continue
                specs.append(OptionContractSpec(
                    occ_symbol=p.occ_symbol, underlying_symbol=sym, right=p.right, strike=p.strike,
                    expiration_date=p.expiration_date, multiplier=100, root_symbol=p.root_symbol,
                ))
            meta_default = True
            if specs:
                chain_warns.append("contract_metadata_unavailable_default_multiplier")

        # filter + sort: nearest-ATM first within each expiry (so IV budget goes to liquid strikes)
        spot_f = float(spot)
        selected: list[OptionContractSpec] = []
        for s in specs:
            dte = calendar_dte(s.expiration_date, now)
            if dte < min_dte or dte > max_dte:
                continue
            if not (lo <= s.strike <= hi):
                continue
            selected.append(s)
        selected.sort(key=lambda s: (abs(float(s.strike) - spot_f), s.expiration_date, s.right.value))

        budget = {"left": self.max_local_iv_solves}
        quotes: list[OptionQuoteSnapshot] = []
        for s in selected:
            extra = ["contract_metadata_unavailable_default_multiplier"] if meta_default else []
            quotes.append(self._build_quote(
                s, snaps.get(s.occ_symbol), spot, now=now, market_open=market_open,
                tier=tier, indicative=indicative, delayed=delayed, solve_budget=budget, extra_warnings=extra,
            ))
        quotes.sort(key=lambda q: (q.contract.expiration_date, q.contract.strike, q.contract.right.value))

        missing = sum(1 for q in quotes if WARN_MISSING_QUOTE in q.data_warnings)
        local = sum(1 for q in quotes if q.greeks.computed_locally)
        if missing:
            chain_warns.append(f"{WARN_MISSING_QUOTE}:{missing}_of_{len(quotes)}")
        if local:
            chain_warns.append(f"{WARN_LOCAL_GREEKS}:{local}_of_{len(quotes)}")
        if budget["left"] <= 0:
            chain_warns.append("local_iv_budget_exhausted")
        if not quotes:
            chain_warns.append("no_contracts_in_filter")
        if not metadata_ok and not snaps:
            tier = DataFeedTier.UNAVAILABLE

        stale_n = sum(1 for q in quotes if q.is_stale)
        chain_stale = (not market_open) or not quotes or stale_n > len(quotes) / 2
        self._last_success_at = self._last_success_at or now

        return OptionChainResponse(
            underlying_symbol=sym,
            underlying_price=spot,
            as_of=now,
            market_open=market_open,
            feed_tier=tier,
            is_indicative=indicative,
            is_delayed_trades=delayed,
            is_stale=chain_stale,
            expirations=sorted({q.contract.expiration_date for q in quotes}),
            contracts=quotes,
            warnings=list(dict.fromkeys(chain_warns)),
            provider_name=self.provider_name,
        )

    # ------------------------------------------------------- positions quotes
    def get_quotes_for_occ(
        self,
        occ_symbols: list[str],
        *,
        underlying_hint: str | None = None,
    ) -> dict[str, OptionQuoteSnapshot]:
        now = self._now()
        market_open = is_nyse_market_open(now)
        out: dict[str, OptionQuoteSnapshot] = {}
        parsed: dict[str, Any] = {}
        for raw in occ_symbols or []:
            occ = str(raw).strip().upper().replace(" ", "")
            if is_valid_occ_symbol(occ):
                parsed[occ] = parse_occ_symbol(occ)
            else:
                self._record_error("invalid_occ_symbol_skipped")
        if not parsed or not self.has_credentials:
            if parsed and not self.has_credentials:
                self._record_error(WARN_CREDENTIALS_MISSING)
            return out

        occs = sorted(parsed)
        cache_key = tuple(occs)
        cached = self._quote_cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        # snapshots in chunks of 100 symbols
        snaps: dict[str, Any] = {}
        eff_feed = "indicative"
        feed_warns: list[str] = []
        fetch_failed = False
        for i in range(0, len(occs), 100):
            chunk = occs[i:i + 100]
            try:
                m, _t, eff_feed, w = self._fetch_snapshots(
                    "/v1beta1/options/snapshots", {"symbols": ",".join(chunk)}, 1
                )
                snaps.update(m)
                feed_warns.extend(w)
            except (AlpacaHTTPError, AlpacaRateLimitTimeout):
                fetch_failed = True
        tier, indicative, delayed = self._tier_flags(eff_feed)

        spots: dict[str, tuple[Optional[Decimal], list[str]]] = {}
        budget = {"left": self.max_local_iv_solves}
        hint = underlying_hint.strip().upper() if underlying_hint else None
        for occ in occs:
            p = parsed[occ]
            und = hint if (hint and len({x.underlying_symbol for x in parsed.values()}) == 1) else p.underlying_symbol
            if und not in spots:
                sp, _o, sw = self.get_underlying_price(und)
                spots[und] = (sp, sw)
            sp, sw = spots[und]
            spec, spec_warns = self._lookup_spec(occ)
            extra = list(spec_warns) + list(feed_warns)
            if sp is None:
                extra.append(WARN_UNDERLYING_UNAVAILABLE)
            if fetch_failed and occ not in snaps:
                extra.append("snapshot_fetch_failed")
            out[occ] = self._build_quote(
                spec, snaps.get(occ), sp, now=now, market_open=market_open, tier=tier,
                indicative=indicative, delayed=delayed, solve_budget=budget, extra_warnings=extra,
            )
        if not fetch_failed:
            self._quote_cache.set(cache_key, dict(out))
        return out

    # ----------------------------------------------------------------- health
    def get_provider_health(self) -> dict:
        now = self._now()
        tier, indicative, delayed = self._tier_flags(
            "opra" if (self.feed == "opra" and not self._opra_denied) else "indicative"
        )
        caches = {
            "stock": self._stock_cache.stats(),
            "contracts": self._contract_cache.stats(),
            "snapshots": self._snapshot_cache.stats(),
            "contract_specs": self._spec_cache.stats(),
            "quotes": self._quote_cache.stats(),
        }
        hits = sum(c["hits"] for c in caches.values())
        misses = sum(c["misses"] for c in caches.values())
        return {
            "provider": self.provider_name,
            "ok": self.has_credentials and self._last_error is None,
            "credentials_configured": self.has_credentials,
            "feed_requested": self.feed,
            "feed_tier": tier.value,
            "is_indicative": indicative,
            "is_delayed_trades": delayed,
            "opra_denied": self._opra_denied,
            "stock_feed": self.stock_feed,
            "market_open": is_nyse_market_open(now),
            "rate_limit": {**self._limiter.stats(), "http": dict(self._http)},
            "cache": {"hits": hits, "misses": misses,
                      "hit_ratio": round(hits / (hits + misses), 4) if (hits + misses) else None,
                      "by_cache": caches},
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at else None,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "checked_at": now.isoformat(),
        }
