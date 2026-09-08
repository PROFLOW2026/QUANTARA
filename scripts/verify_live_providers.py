#!/usr/bin/env python3
"""Live Alpaca + Tiingo verification for QUANTARA 8-asset routing."""

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.core.config import settings  # noqa: E402

US = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
GAP = 1.2


def load_env_keys() -> dict[str, bool]:
    env = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
    names = ["TIINGO_API_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"]
    present = {}
    for n in names:
        present[n] = any(l.startswith(f"{n}=") and len(l.split("=", 1)[1].strip()) > 0 for l in env.splitlines())
    return present


def http_json(url: str, headers: dict | None = None) -> tuple[int, dict | list | str]:
    time.sleep(GAP)
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:300]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def bar_summary(payload: dict | list) -> dict:
    if isinstance(payload, dict) and payload.get("_error"):
        return {"ok": False, "error": payload["_error"]}
    if isinstance(payload, dict) and payload.get("s") == "no_data":
        return {"ok": False, "error": "no_data"}
    if isinstance(payload, list):
        if not payload:
            return {"ok": False, "error": "empty list"}
        last = payload[-1]
        return {
            "ok": True,
            "count": len(payload),
            "latest": last.get("date") or last.get("datetime") or last.get("t"),
            "close": last.get("close") or last.get("c"),
        }
    if isinstance(payload, dict):
        bars = payload.get("bars")
        if isinstance(bars, list) and bars:
            last = bars[-1]
            return {
                "ok": True,
                "count": len(bars),
                "latest": last.get("t") or last.get("timestamp"),
                "close": last.get("c") or last.get("close"),
            }
        if isinstance(bars, dict):
            sym = next(iter(bars))
            rows = bars[sym]
            if not rows:
                return {"ok": False, "error": "empty bars"}
            last = rows[-1]
            return {
                "ok": True,
                "count": len(rows),
                "latest": last.get("t") or last.get("timestamp"),
                "close": last.get("c") or last.get("close"),
            }
        t = payload.get("t") or payload.get("timestamp")
        if isinstance(t, list) and t:
            return {"ok": True, "count": len(t), "latest": t[-1]}
    return {"ok": False, "error": str(payload)[:120]}


def probe_alpaca() -> dict:
    key = settings.alpaca_api_key_id.strip()
    secret = settings.alpaca_api_secret_key.strip()
    feed = (settings.alpaca_data_feed or "iex").strip() or "iex"
    base = settings.alpaca_data_base_url.rstrip("/")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict = {"feed": feed, "assets": {}}

    for sym in US:
        asset = {}
        for label, tf in [("5m", "5Min"), ("15m", "15Min"), ("1h", "1Hour")]:
            url = (
                f"{base}/v2/stocks/{sym}/bars?"
                + urllib.parse.urlencode(
                    {"timeframe": tf, "start": start, "limit": 5, "feed": feed}
                )
            )
            status, body = http_json(url, headers)
            asset[label] = {"http": status, **bar_summary(body if isinstance(body, dict) else {"bars": body})}
        out["assets"][sym] = asset

    for label, tf in [("5m", "5Min"), ("15m", "15Min"), ("1h", "1Hour")]:
        url = (
            f"{base}/v1beta3/crypto/us/bars?"
            + urllib.parse.urlencode(
                {"symbols": "BTC/USD", "timeframe": tf, "start": start, "limit": 5}
            )
        )
        status, body = http_json(url, headers)
        out.setdefault("assets", {})["BTC/USD"] = out["assets"].get("BTC/USD", {})
        out["assets"]["BTC/USD"][label] = {
            "http": status,
            **bar_summary(body if isinstance(body, dict) else {}),
        }
    return out


def tiingo_headers() -> dict:
    token = settings.tiingo_api_key.strip()
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def probe_tiingo() -> dict:
    token = settings.tiingo_api_key.strip()
    headers = tiingo_headers()
    out: dict = {"assets": {}}
    start = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

    for sym in US:
        asset = {}
        for label, freq in [("5m", "5min"), ("15m", "15min"), ("1h", "1hour"), ("1m", "1min")]:
            url = (
                f"https://api.tiingo.com/iex/{sym}/prices?"
                + urllib.parse.urlencode({"startDate": start, "resampleFreq": freq})
            )
            status, body = http_json(url, headers)
            asset[label] = {"http": status, **bar_summary(body if isinstance(body, list) else {"_error": body})}
        out["assets"][sym] = asset

    for ticker in ["btcusd", "BTCUSD"]:
        url = (
            "https://api.tiingo.com/tiingo/crypto/prices?"
            + urllib.parse.urlencode({"tickers": ticker, "startDate": start, "resampleFreq": "5min"})
        )
        status, body = http_json(url, headers)
        summary = bar_summary(body if isinstance(body, list) else {"_error": body})
        if summary.get("ok"):
            out["assets"]["BTC/USD"] = {"5m": {"http": status, **summary}, "ticker": ticker}
            break
        out["assets"]["BTC/USD"] = {"5m": {"http": status, **summary}, "ticker": ticker}
    return out


def recommend(alpaca: dict, tiingo: dict) -> dict[str, str]:
    routing: dict[str, str] = {"XAU/USD": "twelve_data", "EUR/USD": "twelve_data"}
    for sym in US + ["BTC/USD"]:
        a_ok = alpaca.get("assets", {}).get(sym, {}).get("5m", {}).get("ok")
        t_ok = tiingo.get("assets", {}).get(sym, {}).get("5m", {}).get("ok")
        if a_ok:
            routing[sym if sym != "BTC/USD" else "BTC/USD"] = "alpaca"
        elif t_ok:
            routing[sym if sym != "BTC/USD" else "BTC/USD"] = "tiingo"
        else:
            routing[sym if sym != "BTC/USD" else "BTC/USD"] = "BLOCKED"
    return routing


def main() -> int:
    keys = load_env_keys()
    print("QUANTARA LIVE PROVIDER VERIFICATION")
    print("=" * 72)
    print("Keys configured:", {k: ("YES" if v else "NO") for k, v in keys.items()})

    if not settings.alpaca_api_key_id.strip() or not settings.alpaca_api_secret_key.strip():
        print("Alpaca keys missing")
        return 1
    if not settings.tiingo_api_key.strip():
        print("Tiingo key missing")
        return 1

    alpaca = probe_alpaca()
    tiingo = probe_tiingo()
    routing = recommend(alpaca, tiingo)

    print("\nALPACA 5m summary:")
    for sym, data in alpaca.get("assets", {}).items():
        s5 = data.get("5m", {})
        print(f"  {sym}: ok={s5.get('ok')} http={s5.get('http')} count={s5.get('count')} latest={s5.get('latest')}")

    print("\nTIINGO 5m summary:")
    for sym, data in tiingo.get("assets", {}).items():
        s5 = data.get("5m", {})
        print(f"  {sym}: ok={s5.get('ok')} http={s5.get('http')} count={s5.get('count')} latest={s5.get('latest')}")

    print("\nRecommended routing:", routing)
    blocked = [k for k, v in routing.items() if v == "BLOCKED"]
    if blocked:
        print("BLOCKED assets:", blocked)
        return 1

    out = ROOT / "audit_live_providers.json"
    out.write_text(json.dumps({"alpaca": alpaca, "tiingo": tiingo, "routing": routing}, indent=2), encoding="utf-8")
    print(f"\nWrote {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
