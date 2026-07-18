"""Deterministic network-free broker adapter."""

from hashlib import sha256
from typing import assert_never

from quantinue.broker.contracts import OrderPlan
from quantinue.broker.reservations import (
    CompletedClaim,
    InFlightClaim,
    InMemoryOrderReservations,
    OrderReservations,
    OwnerClaim,
)
from quantinue.core.contracts import OrderResult
from quantinue.core.errors import TransientFailureError


class MockBroker:
    """Network-free deterministic fill simulator."""

    def __init__(self, reservations: OrderReservations | None = None) -> None:
        """Use a private reservation adapter unless one is shared explicitly."""
        self._reservations = reservations or InMemoryOrderReservations()

    async def submit(self, plan: OrderPlan) -> OrderResult:
        """Return and cache an immediate full fill."""
        claim = await self._reservations.claim(plan.client_order_id)
        match claim:
            case CompletedClaim(result=result):
                return result
            case InFlightClaim():
                completed = await self._reservations.wait(plan.client_order_id, 1.0)
                if completed is not None:
                    return completed
                provider = "mock"
                reason = "reservation owner did not complete"
                raise TransientFailureError(provider, reason)
            case OwnerClaim(owner_token=owner_token):
                digest = sha256(plan.client_order_id.encode()).hexdigest()[:12]
            case unreachable:
                assert_never(unreachable)
        result = OrderResult(
            order_id=f"mock-{digest}",
            client_order_id=plan.client_order_id,
            status="filled",
            quantity=plan.quantity,
            filled_avg_price=plan.entry_price,
            parent_order_id=f"mock-{digest}",
            stop_leg_order_id=f"mock-{digest}-stop",
            take_profit_leg_order_id=f"mock-{digest}-take-profit",
        )
        published = await self._reservations.complete(plan.client_order_id, owner_token, result)
        if published:
            return result
        winner = await self._reservations.wait(plan.client_order_id, 0)
        if winner is not None:
            return winner
        provider = "mock"
        reason = "reservation generation changed without a completed result"
        raise TransientFailureError(provider, reason)
