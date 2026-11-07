#!/usr/bin/env python3
"""Isolated pre-market readiness checks for QUANTARA Live Sim B.

Does NOT mutate live-sim production accounts. Uses pytest + read-only prod audit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
EXPECTED_ANCHOR = "2026-11-07T22:06:34.448502+00:00"


def run_pytest(args: list[str]) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=line", *args]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ENGINE)
    r = subprocess.run(
        cmd,
        cwd=str(ENGINE),
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    return {
        "cmd": " ".join(args),
        "rc": r.returncode,
        "ok": r.returncode == 0,
        "tail": ((r.stdout or "") + (r.stderr or ""))[-1500:],
    }


def audit_live_experiment() -> dict:
    sys.path.insert(0, str(ENGINE))
    from sqlalchemy import text
    from quantara_engine.core.config import settings
    from quantara_engine.db.session import session_scope
    from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url

    normalize_sqlalchemy_postgres_url(settings.database_url)
    with session_scope() as s:
        rows = s.execute(
            text(
                """
                SELECT slug, cash, equity, starting_cash, realized_pnl, unrealized_pnl,
                       gross_exposure, account_metadata->>'v32_observation_anchor' AS anchor
                FROM broker_accounts
                WHERE slug LIKE 'live-sim%'
                ORDER BY slug
                """
            )
        ).mappings().all()
        by = {r["slug"]: dict(r) for r in rows}
        open_pos = s.execute(
            text(
                """
                SELECT count(*) FROM live_sim_positions lsp
                JOIN broker_accounts ba ON ba.id = lsp.broker_account_id
                WHERE ba.slug LIKE 'live-sim%' AND lsp.status = 'open'
                """
            )
        ).scalar()
        pending = s.execute(
            text(
                """
                SELECT count(*) FROM broker_orders bo
                JOIN broker_accounts ba ON ba.id = bo.broker_account_id
                WHERE ba.slug LIKE 'live-sim%'
                  AND bo.status IN ('created','validating','accepted','submitted','partially_filled')
                """
            )
        ).scalar()
        lots = s.execute(
            text(
                """
                SELECT count(*) FROM broker_attribution_lots bal
                JOIN broker_accounts ba ON ba.id = bal.broker_account_id
                WHERE ba.slug LIKE 'live-sim%' AND bal.remaining_qty > 0
                """
            )
        ).scalar()

    ibkr = by.get("live-sim-ibkr-like", {})
    kraken = by.get("live-sim-kraken-like", {})
    parent = by.get("live-sim-10k", {})
    total = Decimal(str(ibkr.get("equity") or 0)) + Decimal(str(kraken.get("equity") or 0))
    ok = (
        open_pos == 0
        and pending == 0
        and lots == 0
        and Decimal(str(ibkr.get("starting_cash") or 0)) == Decimal("7500")
        and Decimal(str(kraken.get("starting_cash") or 0)) == Decimal("2500")
        and Decimal(str(parent.get("starting_cash") or 0)) == Decimal("10000")
        and total == Decimal("10000")
        and str(ibkr.get("anchor") or "") == EXPECTED_ANCHOR
        and Decimal(str(ibkr.get("realized_pnl") or 0)) == 0
        and Decimal(str(kraken.get("realized_pnl") or 0)) == 0
    )
    return {
        "ok": ok,
        "open_pos": open_pos,
        "pending": pending,
        "lots": lots,
        "total_equity": str(total),
        "accounts": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in by.items()},
    }


def main() -> int:
    cases = []
    suites = [
        [
            "tests/test_live_sim_v2.py",
            "tests/test_exit_flip_attribution_orphan.py",
            "tests/test_broker_realism.py::test_dust_close_exempt_from_min_notional",
            "tests/test_broker_realism.py::test_residual_dust_below_min_quantity_full_flatten_allowed",
            "tests/test_broker_replay_v3.py",
            "tests/test_broker_attribution_flip.py",
            "tests/test_live_sim_lifecycle_integrity.py",
            "tests/test_live_sim_containment_lifecycle.py",
            "tests/test_live_sim_entry_authority.py",
            "tests/test_live_sim_execution_fix.py",
        ],
        [
            "tests/test_live_sim_spot_crypto_execution.py",
            "tests/test_live_sim_cde_fx_envelope.py",
            "tests/test_multi_market_live_sim_eligibility.py",
        ],
    ]
    for suite in suites:
        res = run_pytest(suite)
        cases.append({"name": "pytest:" + ",".join(Path(a).name for a in suite[:3]) + "...", **res})

    audit = audit_live_experiment()
    cases.append({"name": "live_experiment_10k_clean", **audit})

    # Runtime process probe (read-only)
    try:
        import psutil  # type: ignore
        procs = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "uvicorn main:app" in cmd or "quantara_workers.main" in cmd:
                procs.append({"pid": p.info["pid"], "cmd": cmd[:120]})
        runtime_ok = any("uvicorn" in p["cmd"] for p in procs) and any(
            "quantara_workers" in p["cmd"] for p in procs
        )
        cases.append({"name": "runtime_engine_workers", "ok": runtime_ok, "procs": procs})
    except Exception:
        # Fallback without psutil
        import subprocess as sp

        out = sp.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn main:app|quantara_workers.main' } | Select-Object -ExpandProperty ProcessId",
            ],
            capture_output=True,
            text=True,
        )
        pids = [x for x in (out.stdout or "").split() if x.isdigit()]
        cases.append({"name": "runtime_engine_workers", "ok": len(pids) >= 2, "pids": pids})

    passed = sum(1 for c in cases if c.get("ok"))
    failed = len(cases) - passed
    report = {"passed": passed, "failed": failed, "ok": failed == 0, "cases": cases}
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
