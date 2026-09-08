#!/usr/bin/env python3
"""Alpaca free-tier capability audit (docs + optional live credentials)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.core.config import settings  # noqa: E402

US_SYMBOLS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
CRYPTO_SYMBOL = "BTC/USD"
TIMEFRAMES = ["1Min", "5Min", "15Min", "1Hour"]


def _get(url: str, headers: dict | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body[:300]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body[:300]
        return exc.code, payload


def alpaca_headers() -> dict | None:
    key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or ""
    secret = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY") or ""
    if not key or not secret:
        return None
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def probe_stock(symbol: str, headers: dict) -> dict:
    out: dict = {"symbol": symbol, "supported": "UNKNOWN", "live_feed": "IEX (Basic)", "rest_bars": {}}
    for tf in TIMEFRAMES:
        url = (
            f"https://data.alpaca.markets/v2/stocks/bars?"
            f"symbols={symbol}&timeframe={tf}&limit=3&feed=iex"
        )
        status, payload = _get(url, headers)
        ok = status == 200 and isinstance(payload, dict) and payload.get("bars", {}).get(symbol)
        out["rest_bars"][tf] = "YES" if ok else f"NO ({status})"
    out["supported"] = "YES" if any(v == "YES" for v in out["rest_bars"].values()) else "NO"
    return out


def probe_crypto(symbol: str, headers: dict | None) -> dict:
    out: dict = {"symbol": symbol, "supported": "UNKNOWN", "live_feed": "crypto/us", "rest_bars": {}}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    for tf in TIMEFRAMES:
        url = (
            "https://data.alpaca.markets/v1beta3/crypto/us/bars?"
            f"symbols={urllib.parse.quote(symbol)}&timeframe={tf}"
            f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=3"
        )
        status, payload = _get(url, headers)
        bars = None
        if isinstance(payload, dict):
            bars = payload.get("bars", {}).get(symbol)
        ok = status == 200 and bars
        out["rest_bars"][tf] = "YES" if ok else f"NO ({status})"
    out["supported"] = "YES" if any(v == "YES" for v in out["rest_bars"].values()) else "NO"
    return out


def main() -> None:
    print("ALPACA FREE TIER CAPABILITY AUDIT")
    print("=" * 60)
    headers = alpaca_headers()
    print(f"Credentials configured: {'YES' if headers else 'NO'}")
    print()

    if headers:
        print("US STOCKS/ETFs (feed=iex, Basic plan):")
        for sym in US_SYMBOLS:
            r = probe_stock(sym, headers)
            print(f"  {sym}: supported={r['supported']} bars={r['rest_bars']}")
    else:
        print("US STOCKS/ETFs: live probe SKIPPED (no Alpaca credentials)")
        print("  Docs: SPY, QQQ, NVDA, AAPL, MSFT are standard US equities/ETFs — PASS on coverage")
        print("  REST bars: 1Min-1Hour supported via timeframe param, feed=iex required on Basic")
        print("  Real-time: wss://stream.data.alpaca.markets/v2/iex — bars channel available")
        print("  Historical limitation: latest 15 minutes restricted on SIP; IEX feed OK on Basic")

    print()
    print("CRYPTO (BTC/USD) — probing without auth (Alpaca allows unauthenticated crypto historical):")
    crypto = probe_crypto(CRYPTO_SYMBOL, headers)
    print(f"  supported={crypto['supported']} bars={crypto['rest_bars']}")
    print("  Real-time: wss://stream.data.alpaca.markets/v1beta3/crypto/us — minute bars channel")
    print()

    print("PLAN LIMITS (official docs):")
    print("  US real-time feed = IEX only on Basic (acceptable for paper)")
    print("  Crypto real-time feed = Alpaca US + optional Kraken feeds")
    print("  WebSocket stock connection limit = 1 concurrent (Basic)")
    print("  WebSocket symbol limit = 30 trades/quotes; minute bars reportedly unlimited")
    print("  REST rate limit = 200 req/min (Basic market data)")
    print("  Historical catch-up = since 2016; Basic: recent SIP blocked, IEX OK; crypto full history")
    print("  US session = 09:30-16:00 America/New_York (regular); crypto 24/7 UTC")


if __name__ == "__main__":
    main()
