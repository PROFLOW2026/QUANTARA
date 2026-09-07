"""Smoke-test FastAPI endpoints against live Supabase-backed store."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from quantara_engine.core.config import settings  # noqa: E402

CLIENT_HOST = "127.0.0.1" if settings.engine_host in ("0.0.0.0", "::") else settings.engine_host
BASE = f"http://{CLIENT_HOST}:{settings.engine_port}/api/v1"
HEADERS = {"X-API-Key": settings.quantara_api_key, "Accept": "application/json"}


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", settings.engine_host, "--port", str(settings.engine_port)],
        cwd=ENGINE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                _get("/health")
                break
            except Exception:
                time.sleep(1)
        else:
            print("FAIL: engine did not start")
            return 1

        portfolio = _get("/portfolio")
        today = _get("/analytics/today")
        backtests = _get("/backtests")
        decisions = _get("/decisions?limit=5")
        analytics = _get("/analytics/portfolio")

        assert portfolio.get("equity") is not None, "portfolio missing equity"
        assert today.get("scope") == "paper", "analytics/today scope not paper"
        assert "strategy_instance_id" in today, "analytics/today missing instance scope"
        assert isinstance(backtests, list), "backtests not a list"
        assert len(backtests) >= 1, "no backtests in API"
        assert isinstance(decisions, list), "decisions not a list"
        assert analytics.get("equity_curve") is not None, "analytics missing equity_curve"

        print("PASS  API DB-backed reads")
        print(f"  portfolio equity={portfolio.get('equity')}")
        print(f"  today scope={today.get('scope')} decisions={today.get('decisions_count')}")
        print(f"  backtests={len(backtests)} decisions={len(decisions)}")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
