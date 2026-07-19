"""AlpacaBroker answers tradability from /v2/assets before an order exists."""

import httpx2
import pytest
from pydantic import SecretStr

from quantinue.broker.alpaca import AlpacaBroker
from quantinue.core.config import BrokerMode, Settings


def _settings() -> Settings:
    return Settings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=True,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-value"),
        control_room_token=SecretStr("test-control-room-token"),
    )


def _broker(handler: object) -> AlpacaBroker:
    transport = httpx2.MockTransport(handler)  # type: ignore[arg-type]
    return AlpacaBroker(_settings(), transport=transport)


@pytest.mark.anyio
async def test_active_tradable_asset_is_tradable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v2/assets/NVDA"
        return httpx2.Response(
            200, json={"symbol": "NVDA", "status": "active", "tradable": True}, request=request
        )

    assert await _broker(handler).is_tradable("NVDA") is True


@pytest.mark.anyio
async def test_halted_asset_is_not_tradable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"symbol": "NVDA", "status": "active", "tradable": False}, request=request
        )

    assert await _broker(handler).is_tradable("NVDA") is False


@pytest.mark.anyio
async def test_inactive_asset_is_not_tradable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"symbol": "NVDA", "status": "inactive", "tradable": True}, request=request
        )

    assert await _broker(handler).is_tradable("NVDA") is False


@pytest.mark.anyio
async def test_unknown_symbol_is_not_tradable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"message": "asset not found"}, request=request)

    assert await _broker(handler).is_tradable("ZZZZ") is False


@pytest.mark.anyio
async def test_lookup_failure_does_not_block_execution() -> None:
    # A flaky assets endpoint must not become an outage that stops all trading;
    # Alpaca still rejects a genuinely halted symbol at submission.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"message": "boom"}, request=request)

    assert await _broker(handler).is_tradable("NVDA") is True
