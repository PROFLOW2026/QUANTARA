"""Isolated worker job for QUANTARA Options Lab.

Runs on its own executor (`options_engine`) and ONLY executes a cycle when
`opt_engine_state.enabled` is explicitly True. Never touches equity tables or System A.
"""

from __future__ import annotations

import logging

from quantara_engine.db.session import SessionLocal
from quantara_options.api.router import run_options_cycle_in_session
from quantara_options.db.repository import OptionsRepository

log = logging.getLogger(__name__)


def options_cycle_job() -> None:
    session = SessionLocal()
    try:
        repo = OptionsRepository(session, autocommit=False)
        repo.ensure_lab_seeded()
        session.commit()
        state = repo.get_engine_state()
        if not state.enabled:
            return
        report = run_options_cycle_in_session(session, use_fixtures=False)
        log.info(
            "options_cycle_job finished: chains=%s decisions=%s errors=%s",
            report.get("chains_ok"),
            report.get("decisions"),
            report.get("errors"),
        )
    except Exception:
        log.exception("options_cycle_job failed")
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()
