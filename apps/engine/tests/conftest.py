"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from tests.broker_integration_support import provision_broker_test_database


@pytest.fixture(scope="session")
def broker_test_database():
    """Disposable PostgreSQL with migrations through 0006."""
    yield provision_broker_test_database()
