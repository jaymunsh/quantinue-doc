"""Stable broker imports and configuration factory."""

from typing import assert_never

from quantinue.broker.alpaca import AlpacaBroker
from quantinue.broker.contracts import Broker, OrderPlan
from quantinue.broker.mock import MockBroker
from quantinue.broker.reservations import InMemoryOrderReservations
from quantinue.core.config import BrokerMode, Settings


def build_broker(settings: Settings) -> Broker:
    """Select the broker adapter exhaustively from configuration."""
    match settings.broker_mode:
        case BrokerMode.MOCK:
            return MockBroker()
        case BrokerMode.ALPACA:
            return AlpacaBroker(settings)
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "AlpacaBroker",
    "Broker",
    "InMemoryOrderReservations",
    "MockBroker",
    "OrderPlan",
    "build_broker",
]
