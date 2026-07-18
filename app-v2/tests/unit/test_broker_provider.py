"""Behavioral tests for the common Mock and Alpaca PAPER broker boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, assert_never

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from quantinue.broker.provider import AlpacaBroker, InMemoryOrderReservations, MockBroker, OrderPlan
from quantinue.broker.reservations import (
    CompletedClaim,
    InFlightClaim,
    OwnerClaim,
    ReservationClaim,
    ReservationOwnerToken,
)
from quantinue.core.config import BrokerMode, Settings
from quantinue.core.errors import (
    AuthenticationFailureError,
    TradingDisabledError,
    TransientFailureError,
    ValidationFailureError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

PAPER_URL: Final = "https://paper-api.alpaca.markets"


def require_owner_token(claim: ReservationClaim) -> ReservationOwnerToken:
    """Fail a reservation test unless its claim owns the generation."""
    match claim:
        case OwnerClaim(owner_token=owner_token):
            return owner_token
        case InFlightClaim() | CompletedClaim():
            pytest.fail("expected owner claim")
        case unexpected:
            assert_never(unexpected)


def plan(*, quantity: int = 2) -> OrderPlan:
    return OrderPlan(
        ticker="NVDA",
        client_order_id="q-a1-s4022",
        quantity=quantity,
        entry_price=100,
        stop_loss=85,
        take_profit=120,
    )


def settings(*, enabled: bool = True) -> Settings:
    return Settings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=enabled,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-value"),
        control_room_token=SecretStr("test-control-room-token"),
    )


def broker_with(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    selected_settings: Settings | None = None,
    reservations: InMemoryOrderReservations | None = None,
) -> AlpacaBroker:
    return AlpacaBroker(
        selected_settings or settings(),
        transport=httpx2.MockTransport(handler),
        reservations=reservations,
    )


@pytest.mark.anyio
async def test_mock_baseline_returns_observable_full_fill() -> None:
    result = await MockBroker().submit(plan())

    assert result.status == "filled"
    assert result.quantity == 2
    assert result.client_order_id == "q-a1-s4022"


@pytest.mark.anyio
async def test_alpaca_success_posts_fixed_bracket_without_exposing_secrets() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
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

    result = await broker_with(handler).submit(plan())

    assert result.status == "accepted"
    assert len(requests) == 1
    assert requests[0].url == f"{PAPER_URL}/v2/orders"
    assert b"test-value" not in repr(result).encode()


@pytest.mark.anyio
async def test_alpaca_bracket_response_normalizes_parent_and_leg_order_ids() -> None:
    # Given: Alpaca returns its parent order with nested bracket legs in provider order.
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        return httpx2.Response(
            200,
            json={
                "id": "parent-order-id",
                "client_order_id": "q-a1-s4022",
                "status": "accepted",
                "qty": "2",
                "filled_avg_price": None,
                "legs": [
                    {"id": "stop-order-id", "type": "stop"},
                    {"id": "take-profit-order-id", "type": "limit"},
                ],
            },
        )

    # When: the response crosses the Alpaca adapter boundary.
    result = await broker_with(handler).submit(plan())

    # Then: every provider identity is preserved in its broker-independent field.
    assert result.order_id == "parent-order-id"
    assert result.parent_order_id == "parent-order-id"
    assert result.stop_leg_order_id == "stop-order-id"
    assert result.take_profit_leg_order_id == "take-profit-order-id"


@pytest.mark.anyio
async def test_timeout_after_acceptance_reconciles_by_client_order_id() -> None:
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.method == "POST":
            message = "response lost"
            raise httpx2.ReadTimeout(message, request=request)
        return httpx2.Response(
            200,
            json={
                "id": "order-1",
                "client_order_id": "q-a1-s4022",
                "status": "filled",
                "qty": "2",
                "filled_avg_price": "101.25",
            },
        )

    result = await broker_with(handler).submit(plan())

    assert result.status == "filled"
    assert paths == ["/v2/orders", "/v2/orders:by_client_order_id"]


@pytest.mark.anyio
async def test_timeout_before_acceptance_is_retryable_without_second_post() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if request.method == "POST":
            message = "not accepted"
            raise httpx2.ReadTimeout(message, request=request)
        return httpx2.Response(404)

    with pytest.raises(TransientFailureError):
        _ = await broker_with(handler).submit(plan())

    assert calls == 2


@pytest.mark.anyio
async def test_duplicate_returns_reserved_result_without_second_post() -> None:
    posts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal posts
        posts += request.method == "POST"
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

    broker = broker_with(handler)
    first = await broker.submit(plan())
    second = await broker.submit(plan())

    assert second == first
    assert posts == 1


@pytest.mark.anyio
async def test_401_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(401)

    with pytest.raises(AuthenticationFailureError):
        _ = await broker_with(handler).submit(plan())

    assert calls == 1


@pytest.mark.anyio
async def test_malformed_provider_response_is_rejected() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"unexpected": True})

    with pytest.raises(ValidationFailureError):
        _ = await broker_with(handler).submit(plan())


@pytest.mark.anyio
async def test_disabled_trading_and_live_url_make_zero_network_calls() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    disabled = broker_with(handler, selected_settings=settings(enabled=False))
    with pytest.raises(TradingDisabledError):
        _ = await disabled.submit(plan())

    unsafe_settings = Settings.model_construct(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=True,
        alpaca_api_key=settings().alpaca_api_key,
        alpaca_secret_key=settings().alpaca_secret_key,
        alpaca_base_url="https://api.alpaca.markets",
    )
    unsafe = broker_with(handler, selected_settings=unsafe_settings)
    with pytest.raises(TradingDisabledError):
        _ = await unsafe.submit(plan())

    assert calls == 0


@pytest.mark.anyio
async def test_zero_quantity_is_never_submitted() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    with pytest.raises(ValidationError):
        _ = plan(quantity=0)
    assert calls == 0
