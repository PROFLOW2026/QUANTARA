"""Shared helpers for QUANTARA Options market-data providers.

Contents
--------
* OCC option symbol parser / builder.
* ``spread_pct`` (percent of mid) helper.
* NYSE trading calendar (holidays, early closes) + market-open helpers.
* ``TTLCache`` with hit/miss statistics.
* ``TokenBucket`` rate limiter with statistics.
* Time-to-expiry / DTE helpers and closed-market warning helper.

Pure stdlib. Contains NO network code and NO credentials handling.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from quantara_options.contracts import OptionRight

# ---------------------------------------------------------------------------
# Warning codes (shared vocabulary across providers)
# ---------------------------------------------------------------------------
WARN_MARKET_CLOSED = "market_closed"
WARN_INDICATIVE_FEED = "indicative_feed_only"
WARN_DELAYED_TRADES = "delayed_trades_15min"
WARN_MISSING_QUOTE = "missing_quote"
WARN_STALE_QUOTE = "stale_quote"
WARN_CROSSED_QUOTE = "crossed_quote"
WARN_NO_BID = "no_bid"
WARN_NO_ASK = "no_ask"
WARN_WIDE_SPREAD = "wide_spread"
WARN_LOCAL_IV = "local_iv_computed"
WARN_LOCAL_GREEKS = "local_greeks_computed"
WARN_IV_UNAVAILABLE = "iv_unavailable"
WARN_IV_FROM_LAST = "iv_from_last_trade"
WARN_IV_FROM_CLOSE = "iv_from_close_price"
WARN_CONTRACT_EXPIRED = "contract_expired"
WARN_NONSTANDARD_MULTIPLIER = "nonstandard_multiplier"
WARN_SYNTHETIC_FIXTURE = "synthetic_fixture_not_live_market"
WARN_CREDENTIALS_MISSING = "alpaca_credentials_missing"
WARN_UNDERLYING_UNAVAILABLE = "underlying_price_unavailable"

WIDE_SPREAD_PCT = 25.0

# ---------------------------------------------------------------------------
# Timezone (America/New_York) with a rule-based fallback if tzdata is absent
# ---------------------------------------------------------------------------


class _USEasternFallback(tzinfo):  # pragma: no cover - only used without tzdata
    _STD = timedelta(hours=-5)
    _DST = timedelta(hours=-4)

    @staticmethod
    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def _dst_range(self, year: int) -> tuple[datetime, datetime]:
        start = datetime.combine(self._nth_weekday(year, 3, 6, 2), datetime.min.time()).replace(hour=2)
        end = datetime.combine(self._nth_weekday(year, 11, 6, 1), datetime.min.time()).replace(hour=2)
        return start, end

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return self._STD
        start, end = self._dst_range(dt.year)
        naive = dt.replace(tzinfo=None)
        return self._DST if start <= naive < end else self._STD

    def dst(self, dt: Optional[datetime]) -> timedelta:
        return timedelta(hours=1) if self.utcoffset(dt) == self._DST else timedelta(0)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "EDT" if self.dst(dt) else "EST"


def _load_et() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/New_York")
    except Exception:  # pragma: no cover
        return _USEasternFallback()


NY_TZ: tzinfo = _load_et()
MARKET_OPEN_TIME = (9, 30)
MARKET_CLOSE_TIME = (16, 0)
EARLY_CLOSE_TIME = (13, 0)


def ensure_utc(dt: datetime | None) -> datetime:
    """Return an aware UTC datetime (naive input is treated as UTC)."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_ny(dt: datetime | None = None) -> datetime:
    return ensure_utc(dt).astimezone(NY_TZ)


# ---------------------------------------------------------------------------
# NYSE calendar
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date, *, saturday_to_friday: bool = True) -> Optional[date]:
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_to_friday else None
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


_HOLIDAY_CACHE: dict[int, dict[date, str]] = {}


def nyse_holidays(year: int) -> dict[date, str]:
    """Full-day NYSE closures for ``year`` (rule based, observed dates)."""
    cached = _HOLIDAY_CACHE.get(year)
    if cached is not None:
        return cached
    out: dict[date, str] = {}

    def add(d: Optional[date], name: str) -> None:
        if d is not None and d.year == year:
            out[d] = name

    add(_observed(date(year, 1, 1), saturday_to_friday=False), "New Year's Day")
    add(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    add(_nth_weekday(year, 2, 0, 3), "Washington's Birthday")
    add(_easter(year) - timedelta(days=2), "Good Friday")
    add(_last_weekday(year, 5, 0), "Memorial Day")
    if year >= 2022:
        add(_observed(date(year, 6, 19)), "Juneteenth")
    add(_observed(date(year, 7, 4)), "Independence Day")
    add(_nth_weekday(year, 9, 0, 1), "Labor Day")
    add(_nth_weekday(year, 11, 3, 4), "Thanksgiving Day")
    add(_observed(date(year, 12, 25)), "Christmas Day")
    _HOLIDAY_CACHE[year] = out
    return out


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    is_trading_day: bool
    open_dt: Optional[datetime]  # tz-aware, America/New_York
    close_dt: Optional[datetime]
    early_close: bool = False
    holiday_name: Optional[str] = None


def nyse_session(d: date) -> MarketSession:
    if d.weekday() >= 5:
        return MarketSession(d, False, None, None, False, "Weekend")
    hol = nyse_holidays(d.year).get(d)
    if hol is not None:
        return MarketSession(d, False, None, None, False, hol)

    early = False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + timedelta(days=1):
        early = True
    elif d.month == 7 and d.day == 3:
        early = True
    elif d.month == 12 and d.day == 24:
        early = True

    o = datetime(d.year, d.month, d.day, *MARKET_OPEN_TIME, tzinfo=NY_TZ)
    ch, cm = EARLY_CLOSE_TIME if early else MARKET_CLOSE_TIME
    c = datetime(d.year, d.month, d.day, ch, cm, tzinfo=NY_TZ)
    return MarketSession(d, True, o, c, early, None)


def is_nyse_market_open(now: datetime | None = None) -> bool:
    ny = to_ny(now)
    sess = nyse_session(ny.date())
    if not sess.is_trading_day or sess.open_dt is None or sess.close_dt is None:
        return False
    return sess.open_dt <= ny < sess.close_dt


def next_nyse_open(now: datetime | None = None) -> datetime:
    ny = to_ny(now)
    d = ny.date()
    for _ in range(14):
        sess = nyse_session(d)
        if sess.is_trading_day and sess.open_dt is not None and sess.open_dt > ny:
            return sess.open_dt.astimezone(timezone.utc)
        d += timedelta(days=1)
    raise RuntimeError("no NYSE session found in next 14 days")  # pragma: no cover


def market_status_text(now: datetime | None = None) -> str:
    ny = to_ny(now)
    sess = nyse_session(ny.date())
    if is_nyse_market_open(now):
        return "open"
    if not sess.is_trading_day:
        return f"closed_{(sess.holiday_name or 'non_trading_day').lower().replace(' ', '_').replace(chr(39), '')}"
    assert sess.open_dt is not None
    return "closed_pre_market" if ny < sess.open_dt else "closed_after_hours"


def closed_market_warnings(market_open: bool, now: datetime | None = None) -> list[str]:
    if market_open:
        return []
    status = market_status_text(now)
    if status == "open":  # caller forced "closed" (e.g. fixture override)
        status = "closed_override"
    return [WARN_MARKET_CLOSED, f"market_status:{status}"]


# ---------------------------------------------------------------------------
# Expiry maths
# ---------------------------------------------------------------------------


def expiry_datetime(expiration: date) -> datetime:
    """Option expiry instant: 16:00 New York (13:00 on early-close days)."""
    sess = nyse_session(expiration)
    if sess.close_dt is not None:
        return sess.close_dt.astimezone(timezone.utc)
    return datetime(expiration.year, expiration.month, expiration.day, 16, 0, tzinfo=NY_TZ).astimezone(timezone.utc)


def years_to_expiry(expiration: date, as_of: datetime | None = None) -> float:
    secs = (expiry_datetime(expiration) - ensure_utc(as_of)).total_seconds()
    return max(0.0, secs) / (365.0 * 86400.0)


def calendar_dte(expiration: date, as_of: datetime | None = None) -> int:
    """Calendar days from the New York *date* of ``as_of`` to expiration."""
    return (expiration - to_ny(as_of).date()).days


# ---------------------------------------------------------------------------
# OCC symbols
# ---------------------------------------------------------------------------

_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6}\d?)(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


@dataclass(frozen=True)
class ParsedOCC:
    occ_symbol: str
    root_symbol: str          # as in the symbol (may carry an adjustment digit, e.g. "SPY1")
    underlying_symbol: str    # root without trailing adjustment digit
    expiration_date: date
    right: OptionRight
    strike: Decimal
    is_adjusted_root: bool = False


def parse_occ_symbol(symbol: str) -> ParsedOCC:
    """Parse e.g. ``SPY250620C00500000``. Raises ``ValueError`` on malformed input."""
    if not isinstance(symbol, str):
        raise ValueError("OCC symbol must be a string")
    s = symbol.strip().upper().replace(" ", "")
    m = _OCC_RE.match(s)
    if not m:
        raise ValueError(f"invalid OCC option symbol: {symbol!r}")
    ds = m.group("date")
    try:
        exp = date(2000 + int(ds[0:2]), int(ds[2:4]), int(ds[4:6]))
    except ValueError as exc:
        raise ValueError(f"invalid expiration in OCC symbol: {symbol!r}") from exc
    root = m.group("root")
    adjusted = root[-1].isdigit()
    raw_strike = Decimal(int(m.group("strike"))) / Decimal(1000)
    cents = raw_strike.quantize(Decimal("0.01"))
    strike = cents if cents == raw_strike else raw_strike.quantize(Decimal("0.001"))
    return ParsedOCC(
        occ_symbol=s,
        root_symbol=root,
        underlying_symbol=root[:-1] if adjusted else root,
        expiration_date=exp,
        right=OptionRight.CALL if m.group("cp") == "C" else OptionRight.PUT,
        strike=strike,
        is_adjusted_root=adjusted,
    )


def is_valid_occ_symbol(symbol: str) -> bool:
    try:
        parse_occ_symbol(symbol)
        return True
    except ValueError:
        return False


def build_occ_symbol(root: str, expiration: date, right: OptionRight, strike: Decimal | float) -> str:
    k = int((Decimal(str(strike)) * 1000).to_integral_value())
    cp = "C" if right == OptionRight.CALL else "P"
    return f"{root.upper()}{expiration:%y%m%d}{cp}{k:08d}"


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def to_int(value: Any, default: int = 0) -> int:
    f = to_float(value)
    return default if f is None else int(f)


def spread_pct(bid: Decimal | float | None, ask: Decimal | float | None) -> Optional[float]:
    """Bid/ask spread as a PERCENT of mid (``5.0`` == 5 %). ``None`` if not computable."""
    if bid is None or ask is None:
        return None
    b, a = float(bid), float(ask)
    if a <= 0 or b < 0 or a < b:
        return None
    mid = (a + b) / 2.0
    if mid <= 0:
        return None
    return round((a - b) / mid * 100.0, 3)


def parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse RFC3339 timestamps incl. nanosecond fractions (``...123456789Z``)."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m:
        frac = (m.group(2) or "")[:7]  # '.' + 6 digits
        s = m.group(1) + frac + m.group(3)
    try:
        return ensure_utc(datetime.fromisoformat(s))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

_MISSING = object()


class TTLCache:
    """Small thread-safe TTL cache with hit/miss statistics."""

    def __init__(
        self,
        default_ttl: float = 5.0,
        *,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.default_ttl = float(default_ttl)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: Any, default: Any = None) -> Any:
        now = self._clock()
        with self._lock:
            item = self._data.get(key, _MISSING)
            if item is not _MISSING:
                expires, value = item  # type: ignore[misc]
                if expires > now:
                    self.hits += 1
                    return value
                del self._data[key]
            self.misses += 1
            return default

    def contains(self, key: Any) -> bool:
        """Presence check that does not touch hit/miss statistics."""
        now = self._clock()
        with self._lock:
            item = self._data.get(key, _MISSING)
            return item is not _MISSING and item[0] > now  # type: ignore[index]

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        now = self._clock()
        with self._lock:
            if len(self._data) >= self.max_entries and key not in self._data:
                self._evict(now)
            self._data[key] = (now + (self.default_ttl if ttl is None else float(ttl)), value)

    def _evict(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._data.items() if exp <= now]
        for k in expired:
            del self._data[k]
            self.evictions += 1
        while len(self._data) >= self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]
            self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio": round(self.hits / total, 4) if total else None,
                "size": len(self._data),
                "evictions": self.evictions,
            }


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Thread-safe token-bucket rate limiter (default: 180 requests/minute, burst 10)."""

    def __init__(
        self,
        rate_per_sec: float = 3.0,
        capacity: int = 10,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_sec <= 0 or capacity <= 0:
            raise ValueError("rate_per_sec and capacity must be positive")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()
        self.acquired = 0
        self.waits = 0
        self.rejected = 0
        self.total_wait_seconds = 0.0

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def try_acquire(self, n: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                self.acquired += 1
                return True
            return False

    def acquire(self, n: float = 1.0, *, timeout: float = 10.0) -> bool:
        """Block (up to ``timeout`` seconds) until ``n`` tokens are available."""
        deadline = self._clock() + timeout
        waited = False
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    self.acquired += 1
                    if waited:
                        self.waits += 1
                    return True
                need = (n - self._tokens) / self.rate
            if self._clock() + need > deadline:
                with self._lock:
                    self.rejected += 1
                return False
            waited = True
            with self._lock:
                self.total_wait_seconds += need
            self._sleep(need)

    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._refill()
            return {
                "rate_per_sec": self.rate,
                "capacity": self.capacity,
                "tokens_available": round(self._tokens, 3),
                "acquired": self.acquired,
                "throttled_waits": self.waits,
                "rejected": self.rejected,
                "total_wait_seconds": round(self.total_wait_seconds, 3),
            }
