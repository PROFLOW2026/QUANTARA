"""Hard safety gate — blocks any real external broker submission."""

from __future__ import annotations

from quantara_engine.domain.types import Mode


REAL_BROKER_SUBMISSION_ENABLED = False


def external_broker_submission_allowed(mode: Mode | str | None = None) -> bool:
    """Always False until explicit owner-enabled RealBrokerAdapter integration."""
    return False


def assert_no_external_submission(mode: Mode | str | None = None) -> None:
    if external_broker_submission_allowed(mode):
        raise RuntimeError("External broker submission is disabled in this build")
