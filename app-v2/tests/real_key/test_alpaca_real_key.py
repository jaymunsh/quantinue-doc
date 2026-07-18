"""Alpaca PAPER preflight and separately guarded state-changing order probe."""

from __future__ import annotations

import os
from typing import Final
from uuid import uuid4

import httpx2
import pytest
from pydantic import SecretStr

from quantinue.broker.provider import AlpacaBroker, OrderPlan
from quantinue.core.config import BrokerMode, Settings
from quantinue.db.order_reservations import PostgresOrderReservations

_PAPER_URL: Final = "https://paper-api.alpaca.markets"
_ORDER_OPT_IN: Final = "QUANTINUE_RUN_ALPACA_ORDER_TEST"


def _credentials() -> tuple[str, str]:
    key = os.getenv("QUANTINUE_ALPACA_API_KEY")
    secret = os.getenv("QUANTINUE_ALPACA_SECRET_KEY")
    if not key or not secret:
        pytest.skip("Alpaca PAPER credentials are not set")
    return key, secret


def _headers(key: str, secret: str) -> dict[str, str]:
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _control_room_token() -> str:
    token = os.getenv("QUANTINUE_CONTROL_ROOM_TOKEN")
    if not token:
        pytest.skip("QUANTINUE_CONTROL_ROOM_TOKEN is required when paper trading is enabled")
    return token


@pytest.mark.anyio
@pytest.mark.real_key
async def test_alpaca_paper_credentials_can_read_account() -> None:
    # Given: explicitly opted-in PAPER credentials.
    key, secret = _credentials()

    # When: the read-only account endpoint is queried.
    async with httpx2.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_PAPER_URL}/v2/account", headers=_headers(key, secret))

    # Then: authentication succeeds without changing paper account state.
    _ = response.raise_for_status()
    assert response.json()["id"]


@pytest.mark.anyio
@pytest.mark.real_key
async def test_alpaca_paper_order_uses_postgres_reservation_and_is_canceled() -> None:
    # Given: two explicit opt-ins plus disposable PostgreSQL durable reservation storage.
    if os.getenv(_ORDER_OPT_IN) != "1":
        pytest.skip(f"state-changing PAPER order disabled; set {_ORDER_OPT_IN}=1")
    database_url = os.getenv("QUANTINUE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("QUANTINUE_TEST_DATABASE_URL is required for durable order reservation")
    key, secret = _credentials()
    control_room_token = _control_room_token()
    client_order_id = f"quantinue-real-key-{uuid4().hex[:20]}"
    reservations = PostgresOrderReservations(database_url)
    await reservations.initialize()
    settings = Settings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=True,
        alpaca_api_key=SecretStr(key),
        alpaca_secret_key=SecretStr(secret),
        control_room_token=SecretStr(control_room_token),
    )

    order_id: str | None = None
    try:
        # When: exactly one quantity-one PAPER bracket order is submitted.
        result = await AlpacaBroker(settings, reservations=reservations).submit(
            OrderPlan(
                ticker=os.getenv("QUANTINUE_ALPACA_TEST_TICKER", "SPY"),
                client_order_id=client_order_id,
                quantity=1,
                entry_price=100,
                stop_loss=50,
                take_profit=200,
            )
        )

        # Then: Alpaca accepted an identity protected by the durable reservation.
        assert result.client_order_id == client_order_id
        order_id = result.order_id
    finally:
        if order_id is not None:
            async with httpx2.AsyncClient(timeout=15) as client:
                _ = await client.delete(
                    f"{_PAPER_URL}/v2/orders/{order_id}",
                    headers=_headers(key, secret),
                )
        await reservations.close()
