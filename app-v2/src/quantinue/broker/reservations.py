"""Concurrency-safe duplicate-order reservation boundary and in-memory adapter."""

from dataclasses import dataclass, replace
from enum import StrEnum, unique
from time import monotonic
from typing import Literal, NewType, Protocol
from uuid import uuid4

import anyio

from quantinue.core.contracts import OrderResult


@unique
class ClaimKind(StrEnum):
    """Possible outcomes when reserving a client order ID."""

    OWNER = "owner"
    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"


ReservationOwnerToken = NewType("ReservationOwnerToken", str)


@dataclass(frozen=True, slots=True)
class OwnerClaim:
    """Exclusive right to submit one client order generation."""

    owner_token: ReservationOwnerToken
    kind: Literal[ClaimKind.OWNER] = ClaimKind.OWNER


@dataclass(frozen=True, slots=True)
class InFlightClaim:
    """Another owner is currently submitting this identity."""

    kind: Literal[ClaimKind.IN_FLIGHT] = ClaimKind.IN_FLIGHT


@dataclass(frozen=True, slots=True)
class CompletedClaim:
    """A prior owner already published the normalized result."""

    result: OrderResult
    kind: Literal[ClaimKind.COMPLETED] = ClaimKind.COMPLETED


ReservationClaim = OwnerClaim | InFlightClaim | CompletedClaim


class OrderReservations(Protocol):
    """Narrow protocol persistence can implement transactionally later."""

    async def claim(self, client_order_id: str) -> ReservationClaim:
        """Claim one stable order identity."""
        ...

    async def complete(
        self, client_order_id: str, owner_token: ReservationOwnerToken, result: OrderResult
    ) -> bool:
        """Publish the final normalized result."""
        ...

    async def release(self, client_order_id: str, owner_token: ReservationOwnerToken) -> bool:
        """Release an unsuccessful claim."""
        ...

    async def wait(self, client_order_id: str, timeout_seconds: float) -> OrderResult | None:
        """Await another owner's bounded completion."""
        ...


@dataclass(frozen=True, slots=True)
class _Reservation:
    """Immutable synchronization state owned by InMemoryOrderReservations."""

    claimed_at: float
    owner_token: ReservationOwnerToken
    ready: anyio.Event
    result: OrderResult | None = None


class InMemoryOrderReservations:
    """Process-local reservation adapter with bounded stale-owner recovery."""

    def __init__(self, *, stale_after_seconds: float = 60.0) -> None:
        """Configure when an abandoned reservation may be reclaimed."""
        self._stale_after_seconds = stale_after_seconds
        self._items: dict[str, _Reservation] = {}
        self._lock = anyio.Lock()

    async def claim(self, client_order_id: str) -> ReservationClaim:
        """Atomically claim, join, or reuse a completed submission."""
        async with self._lock:
            current = self._items.get(client_order_id)
            now = monotonic()
            if current is None or (
                current.result is None and now - current.claimed_at >= self._stale_after_seconds
            ):
                if current is not None:
                    current.ready.set()
                owner_token = ReservationOwnerToken(uuid4().hex)
                self._items[client_order_id] = _Reservation(now, owner_token, anyio.Event())
                return OwnerClaim(owner_token)
            if current.result is not None:
                return CompletedClaim(current.result)
            return InFlightClaim()

    async def complete(
        self, client_order_id: str, owner_token: ReservationOwnerToken, result: OrderResult
    ) -> bool:
        """Publish the normalized result to duplicate callers."""
        async with self._lock:
            current = self._items.get(client_order_id)
            if current is None or current.owner_token != owner_token:
                return False
            self._items[client_order_id] = replace(current, result=result)
            current.ready.set()
            return True

    async def release(self, client_order_id: str, owner_token: ReservationOwnerToken) -> bool:
        """Release a failed owner's reservation so a later retry may submit."""
        async with self._lock:
            current = self._items.get(client_order_id)
            if current is None or current.owner_token != owner_token:
                return False
            del self._items[client_order_id]
            current.ready.set()
            return True

    async def wait(self, client_order_id: str, timeout_seconds: float) -> OrderResult | None:
        """Wait boundedly for an in-process owner to publish its result."""
        async with self._lock:
            current = self._items.get(client_order_id)
            if current is None:
                return None
            event = current.ready
        with anyio.move_on_after(timeout_seconds):
            await event.wait()
        async with self._lock:
            resolved = self._items.get(client_order_id)
            return resolved.result if resolved is not None else None
