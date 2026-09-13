"""Future real-broker connectivity interface — NOT enabled in this build."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from quantara_engine.broker.live_execution_gate import external_broker_submission_allowed


@dataclass
class BrokerConnectionConfig:
    rest_base_url: str = ""
    ws_url: str = ""
    account_id: str = ""
    api_key_env: str = ""
    api_secret_env: str = ""
    oauth_enabled: bool = False
    rate_limit_per_second: int = 10
    heartbeat_interval_sec: int = 30
    metadata: dict = field(default_factory=dict)


class BrokerConnector(Protocol):
    """REST/WebSocket connector contract for future RealBrokerAdapter."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def heartbeat(self) -> bool: ...

    def on_order_update(self, callback) -> None: ...

    def on_fill_update(self, callback) -> None: ...

    def on_position_update(self, callback) -> None: ...

    def on_account_update(self, callback) -> None: ...


def connector_submission_enabled() -> bool:
    return external_broker_submission_allowed()
