"""Broker connector instance registry — multi-broker stream architecture (simulation only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from quantara_engine.broker.connector_interface import BrokerConnectionConfig
from quantara_engine.broker.vendor import BrokerConnectionState, BrokerVendor


@dataclass
class BrokerConnectorInstance:
    account_slug: str
    account_id: str
    broker_vendor: BrokerVendor
    config: BrokerConnectionConfig
    connection_state: BrokerConnectionState = BrokerConnectionState.DISCONNECTED
    last_heartbeat_at: datetime | None = None
    last_error: str | None = None
    metadata: dict = field(default_factory=dict)


class SimulatedConnectorStub:
    """Placeholder connector — no external network."""

    def __init__(self, instance: BrokerConnectorInstance) -> None:
        self.instance = instance

    def connect(self) -> None:
        self.instance.connection_state = BrokerConnectionState.CONNECTED
        self.instance.last_heartbeat_at = datetime.now(timezone.utc)

    def disconnect(self) -> None:
        self.instance.connection_state = BrokerConnectionState.DISCONNECTED

    def heartbeat(self) -> bool:
        self.instance.last_heartbeat_at = datetime.now(timezone.utc)
        return True


class BrokerConnectorRegistry:
    """Runtime registry of broker connector instances keyed by account slug."""

    def __init__(self) -> None:
        self._instances: dict[str, BrokerConnectorInstance] = {}
        self._connectors: dict[str, SimulatedConnectorStub] = {}

    def register(
        self,
        *,
        account_slug: str,
        account_id: str,
        broker_vendor: BrokerVendor,
        config: BrokerConnectionConfig | None = None,
    ) -> BrokerConnectorInstance:
        inst = BrokerConnectorInstance(
            account_slug=account_slug,
            account_id=account_id,
            broker_vendor=broker_vendor,
            config=config or BrokerConnectionConfig(account_id=account_id),
            connection_state=BrokerConnectionState.CONNECTED,
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        self._instances[account_slug] = inst
        self._connectors[account_slug] = SimulatedConnectorStub(inst)
        return inst

    def get(self, account_slug: str) -> BrokerConnectorInstance | None:
        return self._instances.get(account_slug)

    def set_connection_state(self, account_slug: str, state: BrokerConnectionState) -> None:
        inst = self._instances.get(account_slug)
        if inst:
            inst.connection_state = state

    def heartbeat_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for slug, conn in self._connectors.items():
            results[slug] = conn.heartbeat()
        return results

    def list_instances(self) -> list[BrokerConnectorInstance]:
        return list(self._instances.values())


_global_registry: BrokerConnectorRegistry | None = None


def get_connector_registry() -> BrokerConnectorRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = BrokerConnectorRegistry()
    return _global_registry
