"""Concurrency and lifecycle tests for broker order reservations."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

import anyio
import httpx2
import pytest
from anyio.lowlevel import checkpoint
from pydantic import SecretStr

from quantinue.broker.provider import (
    AlpacaBroker,
    InMemoryOrderReservations,
    MockBroker,
    OrderPlan,
)
from quantinue.broker.reservations import (
    ClaimKind,
    CompletedClaim,
    InFlightClaim,
    OwnerClaim,
    ReservationClaim,
    ReservationOwnerToken,
)
from quantinue.core.config import BrokerMode, Settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from quantinue.core.contracts import OrderResult


def plan() -> OrderPlan:
    return OrderPlan(
        ticker="NVDA",
        client_order_id="q-a1-s4022",
        quantity=2,
        entry_price=100,
        stop_loss=85,
        take_profit=120,
    )


def settings() -> Settings:
    return Settings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=True,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-value"),
        control_room_token=SecretStr("test-control-room-token"),
    )


def broker_with(handler: Callable[[httpx2.Request], httpx2.Response]) -> AlpacaBroker:
    return AlpacaBroker(settings(), transport=httpx2.MockTransport(handler))


def _owner_token(claim: ReservationClaim) -> ReservationOwnerToken:
    match claim:
        case OwnerClaim(owner_token=token):
            return token
        case CompletedClaim() | InFlightClaim():
            pytest.fail("expected owner claim")
        case unreachable:
            assert_never(unreachable)


@pytest.mark.anyio
async def test_concurrent_same_plan_makes_exactly_one_post() -> None:
    posts = 0

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            await checkpoint()
        return httpx2.Response(
            200,
            json={
                "id": "order-1",
                "client_order_id": "q-a1-s4022",
                "status": "accepted",
                "qty": "2",
                "filled_avg_price": None,
            },
        )

    broker = AlpacaBroker(settings(), transport=httpx2.MockTransport(response))
    results: list[OrderResult] = []

    async def submit() -> None:
        results.append(await broker.submit(plan()))

    async with anyio.create_task_group() as group:
        _ = group.start_soon(submit)
        _ = group.start_soon(submit)

    assert posts == 1
    assert len(results) == 2
    assert results[0] == results[1]


@pytest.mark.anyio
async def test_stale_reservation_can_be_reclaimed() -> None:
    reservations = InMemoryOrderReservations(stale_after_seconds=0)
    first = await reservations.claim("q-a1-s4022")
    second = await reservations.claim("q-a1-s4022")
    assert first.kind is ClaimKind.OWNER
    assert second.kind is ClaimKind.OWNER
    assert _owner_token(first) != _owner_token(second)


@pytest.mark.anyio
async def test_stale_owner_cannot_release_or_overwrite_new_generation() -> None:
    reservations = InMemoryOrderReservations(stale_after_seconds=0)
    old = await reservations.claim("q-a1-s4022")
    new = await reservations.claim("q-a1-s4022")
    old_result = await MockBroker().submit(plan())
    new_result = old_result.model_copy(update={"order_id": "new-owner"})
    assert await reservations.complete("q-a1-s4022", _owner_token(new), new_result)
    assert not await reservations.release("q-a1-s4022", _owner_token(old))
    assert not await reservations.complete("q-a1-s4022", _owner_token(old), old_result)
    completed = await reservations.claim("q-a1-s4022")
    assert isinstance(completed, CompletedClaim)
    assert completed.result == new_result


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_status", "normalized_status"),
    [
        ("submitted", "submitted"),
        ("new", "accepted"),
        ("filled", "filled"),
        ("canceled", "canceled"),
        ("rejected", "rejected"),
    ],
)
async def test_supported_order_lifecycle_is_observable(
    provider_status: str, normalized_status: str
) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "order-1",
                "client_order_id": "q-a1-s4022",
                "status": provider_status,
                "qty": "2",
                "filled_avg_price": "100" if provider_status == "filled" else None,
            },
        )

    result = await broker_with(handler).submit(plan())
    assert result.status == normalized_status
