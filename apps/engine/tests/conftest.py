"""Shared pytest fixtures."""

from __future__ import annotations

import os

# Before any quantara_engine import — settings reads repo .env which may leave TIINGO empty.
os.environ["TIINGO_API_KEY"] = os.environ.get("TIINGO_API_KEY") or "unit-test-tiingo-key"

import pytest

import quantara_engine.core.config as _config_mod

_config_mod.settings.tiingo_api_key = os.environ["TIINGO_API_KEY"]

from tests.broker_integration_support import provision_broker_test_database


@pytest.fixture(scope="session")
def broker_test_database():
    """Disposable PostgreSQL with all forward migrations applied."""
    yield provision_broker_test_database()


@pytest.fixture
def broker_test_store(broker_test_database):
    from quantara_engine.db.session import SessionLocal
    from quantara_engine.persistence.store import TradingStore

    session = SessionLocal()
    store = TradingStore(session)
    try:
        yield store
    finally:
        session.close()
