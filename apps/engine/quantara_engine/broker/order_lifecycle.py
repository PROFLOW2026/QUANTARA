"""Broker order lifecycle transitions — shared by simulated and future real adapters."""

from __future__ import annotations

from quantara_engine.broker.types import BrokerOrderStatus

VALID_TRANSITIONS: dict[BrokerOrderStatus, set[BrokerOrderStatus]] = {
    BrokerOrderStatus.CREATED: {BrokerOrderStatus.VALIDATING, BrokerOrderStatus.REJECTED},
    BrokerOrderStatus.VALIDATING: {
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.SUBMITTED,
    },
    BrokerOrderStatus.ACCEPTED: {BrokerOrderStatus.SUBMITTED, BrokerOrderStatus.REJECTED},
    BrokerOrderStatus.SUBMITTED: {
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.CANCELLED,
    },
    BrokerOrderStatus.PARTIALLY_FILLED: {
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
    },
    BrokerOrderStatus.FILLED: set(),
    BrokerOrderStatus.REJECTED: set(),
    BrokerOrderStatus.CANCELLED: set(),
    BrokerOrderStatus.EXPIRED: set(),
}


def _coerce_status(value: BrokerOrderStatus | str) -> BrokerOrderStatus:
    if isinstance(value, BrokerOrderStatus):
        return value
    text = str(value)
    if text.startswith("BrokerOrderStatus."):
        text = text.split(".", 1)[1]
    return BrokerOrderStatus(text)


def can_transition(current: BrokerOrderStatus | str, target: BrokerOrderStatus | str) -> bool:
    cur = _coerce_status(current)
    tgt = _coerce_status(target)
    return tgt in VALID_TRANSITIONS.get(cur, set())
