#!/usr/bin/env python3
"""Live Finnhub Free capability audit for QUANTARA 8-asset plan."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.core.config import settings  # noqa: E402

BASE = "https://finnhub.io/api/v1"
US_SYMBOLS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
TARGET_8_ASSETS = {
    "BTCUSD": ("crypto", "COINBASE:BTC-USD", "/crypto/candle"),
    "ETHUSD": ("crypto", "COINBASE:ETH-USD", "/crypto/candle"),
    "NVDA": ("stock", "NVDA", "/stock/candle"),
    "TSLA": ("stock", "TSLA", "/stock/candle"),
    "AMD": ("stock", "AMD", "/stock/candle"),
    "COIN": ("stock", "COIN", "/stock/candle"),
    "XAUUSD": ("forex", "OANDA:XAU_USD", "/forex/candle"),
    "GBPJPY": ("forex", "OANDA:GBP_JPY", "/forex/candle"),
}
RESOLUTIONS = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
}
REQUEST_GAP_SEC = 1.1


def load_api_key() -> str:
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        # pydantic settings also loads .env
        key = getattr(settings, "finnhub_api_key", "") if hasattr(settings, "finnhub_api_key") else ""
    if not key:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("FINNHUB_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key


class FinnhubClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = 0
        self.errors: list[str] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        self.calls += 1
        if self.calls > 1:
            time.sleep(REQUEST_GAP_SEC)
        query = {"token": self.token, **(params or {})}
        url = f"{BASE}{path}?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            self.errors.append(f"{path} HTTP {exc.code}: {body}")
            return {"_error": body, "_status": exc.code}
        except urllib.error.URLError as exc:
            self.errors.append(f"{path} network: {exc.reason}")
            return {"_error": str(exc.reason)}


def candle_status(payload: dict[str, Any]) -> tuple[str, str | None]:
    if payload.get("_error"):
        return "NO", str(payload.get("_error"))[:120]
    status = payload.get("s")
    if status != "ok":
        return "NO", f"status={status!r}"
    closes = payload.get("c") or []
    times = payload.get("t") or []
    if not closes:
        return "NO", "empty"
    latest_ts = datetime.fromtimestamp(int(times[-1]), tz=timezone.utc)
    return "YES", latest_ts.isoformat()


def quote_status(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    if payload.get("_error"):
        return "NO", None, str(payload.get("_error"))[:120]
    price = payload.get("c")
    if price is None:
        return "NO", None, "missing close/current price"
    ts = payload.get("t")
    ts_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat() if ts else None
    return "YES", str(price), ts_iso


def probe_stock(client: FinnhubClient, symbol: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    frm = int((now - timedelta(days=3)).timestamp())
    to = int(now.timestamp())

    quote = client.get("/quote", {"symbol": symbol})
    q_ok, q_price, q_ts = quote_status(quote)

    profile = client.get("/stock/profile2", {"symbol": symbol})
    asset_class = "ETF" if symbol in {"SPY", "QQQ"} else "US stock"
    exchange = profile.get("exchange") if isinstance(profile, dict) else None
    if isinstance(profile, dict) and profile.get("finnhubIndustry") == "ETF":
        asset_class = "ETF"

    candles: dict[str, dict[str, str]] = {}
    for label, res in RESOLUTIONS.items():
        payload = client.get(
            "/stock/candle",
            {"symbol": symbol, "resolution": res, "from": frm, "to": to},
        )
        ok, detail = candle_status(payload)
        candles[label] = {"status": ok, "latest": detail or ""}

    return {
        "exact_symbol": symbol,
        "asset_class": asset_class,
        "exchange": exchange or "US",
        "quote": {"status": q_ok, "price": q_price, "timestamp": q_ts},
        "candles": candles,
    }


def resolve_btc_symbol(client: FinnhubClient) -> tuple[str, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []

    for exchange in ("COINBASE", "BINANCE", "KRAKEN"):
        symbols = client.get("/crypto/symbol", {"exchange": exchange})
        if not isinstance(symbols, list):
            continue
        for row in symbols:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "")
            desc = str(row.get("description") or row.get("displaySymbol") or "")
            upper = sym.upper()
            if "BTC" in upper and ("USD" in upper or "USDT" in upper):
                candidates.append(
                    {
                        "symbol": sym,
                        "exchange": exchange,
                        "description": desc,
                    }
                )

    search = client.get("/search", {"q": "BTC USD"})
    if isinstance(search, dict):
        for row in search.get("result") or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "")
            if "BTC" in sym.upper():
                candidates.append(
                    {
                        "symbol": sym,
                        "exchange": row.get("type") or "search",
                        "description": row.get("description") or "",
                    }
                )

    # Prefer COINBASE BTC-USD style, then BINANCE BTCUSDT
    priority = [
        "COINBASE:BTC-USD",
        "BINANCE:BTCUSDT",
        "BINANCE:BTCUSDT",
    ]
    chosen = None
    for pref in priority:
        if any(c["symbol"] == pref for c in candidates):
            chosen = pref
            break
    if not chosen and candidates:
        # pick first COINBASE BTC/USD-like
        for c in candidates:
            if c["exchange"] == "COINBASE" and "BTC" in c["symbol"]:
                chosen = c["symbol"]
                break
    if not chosen and candidates:
        chosen = candidates[0]["symbol"]
    return chosen or "UNKNOWN", candidates[:8]


def probe_crypto(client: FinnhubClient, symbol: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    frm = int((now - timedelta(days=2)).timestamp())
    to = int(now.timestamp())

    # Crypto quote via candle latest close fallback; Finnhub has /crypto/candle primarily
    candles: dict[str, dict[str, str]] = {}
    for label, res in RESOLUTIONS.items():
        payload = client.get(
            "/crypto/candle",
            {"symbol": symbol, "resolution": res, "from": frm, "to": to},
        )
        ok, detail = candle_status(payload)
        candles[label] = {"status": ok, "latest": detail or ""}

    quote_ok = "NO"
    quote_price = None
    quote_ts = None
    if candles.get("5m", {}).get("status") == "YES":
        payload = client.get(
            "/crypto/candle",
            {"symbol": symbol, "resolution": "5", "from": frm, "to": to},
        )
        if isinstance(payload, dict) and payload.get("s") == "ok" and payload.get("c"):
            quote_ok = "YES"
            quote_price = str(payload["c"][-1])
            quote_ts = datetime.fromtimestamp(int(payload["t"][-1]), tz=timezone.utc).isoformat()

    return {
        "exact_symbol": symbol,
        "asset_class": "crypto",
        "exchange": symbol.split(":", 1)[0] if ":" in symbol else "crypto",
        "quote": {"status": quote_ok, "price": quote_price, "timestamp": quote_ts},
        "candles": candles,
    }


def projected_load() -> dict[str, Any]:
    us_assets = 5
    crypto_assets = 1
    poll_interval_min = 5
    us_session_min = 390
    crypto_day_min = 1440

    us_cycles = us_session_min // poll_interval_min
    crypto_cycles = crypto_day_min // poll_interval_min

    rest_per_cycle = us_assets + crypto_assets
    rest_us_day = us_cycles * us_assets
    rest_crypto_day = crypto_cycles * crypto_assets
    # Shared scheduler polls all symbols each cycle when active
    rest_day_all_cycles = crypto_cycles * rest_per_cycle
    rest_peak_per_min = rest_per_cycle / poll_interval_min

    return {
        "rest_5m_model_per_cycle": rest_per_cycle,
        "rest_5m_peak_requests_per_min": round(rest_peak_per_min, 2),
        "rest_5m_requests_day_us_session_only": rest_us_day + rest_crypto_day,
        "rest_5m_requests_day_if_all_symbols_every_5m_24h": rest_day_all_cycles,
        "websocket_symbols_needed": rest_per_cycle,
        "websocket_headroom_vs_50": 50 - rest_per_cycle,
    }


def summarize_asset(result: dict[str, Any]) -> str:
    c = result["candles"]
    return (
        f"symbol={result['exact_symbol']} quote={result['quote']['status']} "
        f"5m={c['5m']['status']} 15m={c['15m']['status']} 1h={c['1h']['status']} "
        f"1m={c['1m']['status']}"
    )


def main() -> int:
    token = load_api_key()
    if not token:
        print("FINNHUB_API_KEY not configured")
        return 1

    client = FinnhubClient(token)
    print("QUANTARA FINNHUB FREE LIVE AUDIT")
    print("=" * 72)
    print(f"API key configured: YES (length={len(token)}, value hidden)")
    print(f"Live probes started: {datetime.now(timezone.utc).isoformat()}")
    print()

    results: dict[str, Any] = {"us": {}, "btc": None, "btc_candidates": []}
    for sym in US_SYMBOLS:
        results["us"][sym] = probe_stock(client, sym)
        print(f"Probed {sym}: {summarize_asset(results['us'][sym])}")

    btc_symbol, candidates = resolve_btc_symbol(client)
    results["btc_candidates"] = candidates
    if btc_symbol != "UNKNOWN":
        results["btc"] = probe_crypto(client, btc_symbol)
        print(f"Probed BTC via {btc_symbol}: {summarize_asset(results['btc'])}")
    else:
        results["btc"] = {"exact_symbol": "UNKNOWN", "quote": {"status": "NO"}, "candles": {}}
        print("BTC symbol resolution FAILED")

    results["target_8"] = {}
    print()
    print("QUANTARA 8-ASSET LIVE PROBE")
    print("-" * 72)
    now = datetime.now(timezone.utc)
    for db_sym, (asset_class, provider_symbol, candle_path) in TARGET_8_ASSETS.items():
        quote = client.get("/quote", {"symbol": provider_symbol})
        q_ok, q_price, q_ts = quote_status(quote)
        frm = int((now - timedelta(days=2)).timestamp())
        to = int(now.timestamp())
        candle = client.get(
            candle_path,
            {"symbol": provider_symbol, "resolution": "1", "from": frm, "to": to},
        )
        c_ok, c_detail = candle_status(candle)
        age_min = None
        rt = "none"
        if q_ts:
            q_dt = datetime.fromisoformat(q_ts)
            age_min = round((now - q_dt).total_seconds() / 60, 1)
            rt = "real-time" if age_min <= 20 else "delayed"
        row = {
            "provider_symbol": provider_symbol,
            "asset_class": asset_class,
            "quote_access": q_ok,
            "quote_freshness_min": age_min,
            "real_time_or_delayed": rt,
            "candle_1m": c_ok,
            "last_price": q_price,
            "timestamp": q_ts,
            "candle_detail": c_detail,
        }
        results["target_8"][db_sym] = row
        print(
            f"{db_sym}: quote={q_ok} fresh_min={age_min} rt={rt} "
            f"1m={c_ok} symbol={provider_symbol} price={q_price} ts={q_ts}"
        )

    load = projected_load()
    print()
    print("FREE TIER (official Finnhub pricing/docs)")
    print("  Requests/min = 60")
    print("  Requests/day = no documented hard daily cap (minute-limited)")
    print("  WebSocket = YES on free (trades/quotes)")
    print("  WS limits = 50 concurrent symbol subscriptions; 1 connection/account noted in community")
    print("  Stock real-time = YES (/quote real-time US on free)")
    print("  ETF real-time = YES (same US quote endpoints; SPY/QQQ use equity symbol format)")
    print("  Crypto real-time = YES (crypto candles/trades; exchange-prefixed symbols)")
    print("  Historical/catch-up = intraday max ~1 month per call; iterate from/to for deeper history")
    print()
    print("PROJECTED LOAD")
    print(f"  REST 5m model peak = {load['rest_5m_peak_requests_per_min']} req/min ({load['rest_5m_model_per_cycle']} symbols / 5 min)")
    print(f"  REST 5m day (US session stocks + 24/7 BTC) = {load['rest_5m_requests_day_us_session_only']} req/day")
    print(f"  REST 5m day (worst case poll all 6 every 5m 24h) = {load['rest_5m_requests_day_if_all_symbols_every_5m_24h']} req/day")
    print(f"  WebSocket model = {load['websocket_symbols_needed']} subscriptions (headroom {load['websocket_headroom_vs_50']}/50)")
    fits = load["rest_5m_peak_requests_per_min"] <= 60 and load["websocket_symbols_needed"] <= 50
    print(f"  Fits free tier = {'YES' if fits else 'NO'}")
    print()
    print(f"Total live API calls made = {client.calls}")
    if client.errors:
        print(f"Errors encountered = {len(client.errors)}")
        for err in client.errors[:5]:
            print(f"  - {err}")

    out_path = ROOT / "audit_finnhub_live.json"
    redacted = json.loads(json.dumps(results))
    out_path.write_text(json.dumps(redacted, indent=2), encoding="utf-8")
    print(f"Detailed probe JSON written to {out_path.name} (no secrets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
