from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import anyio
import pytest

from quantinue.market_data.alpaca_stream import AlpacaTradeStream
from quantinue.market_data.models import LatestTrade
from quantinue.orchestration.watch_policy import WatchStreamConfig


class _Socket:
    def __init__(self, messages: tuple[str, ...]) -> None:
        self._messages = iter(messages)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        try:
            return next(self._messages)
        except StopIteration:
            await anyio.sleep_forever()


@pytest.mark.anyio
async def test_stream_authenticates_subscribes_and_emits_a_normalized_trade() -> None:
    # Given
    socket = _Socket(
        (
            '[{"T":"success","msg":"connected"}]',
            '[{"T":"success","msg":"authenticated"}]',
            '[{"T":"subscription","trades":["BRK.B"]}]',
            '[{"T":"t","S":"BRK.B","p":501.25,"t":"2026-07-20T14:00:00Z"}]',
        )
    )

    @asynccontextmanager
    async def connect() -> AsyncGenerator[_Socket]:
        yield socket

    stream = AlpacaTradeStream(
        key_id="key",
        secret_key="secret",
        config=WatchStreamConfig(enabled=True),
        connector=connect,
    )
    seen: list[LatestTrade] = []
    consumed = anyio.Event()

    async def tickers() -> tuple[str, ...]:
        return ("BRK/B",)

    async def consume(trade: LatestTrade) -> None:
        seen.append(trade)
        consumed.set()

    # When
    async with anyio.create_task_group() as task_group:
        _ = task_group.start_soon(stream.run, tickers, consume)
        await consumed.wait()
        task_group.cancel_scope.cancel()

    # Then
    assert '"action":"auth"' in socket.sent[0]
    assert '"trades":["BRK.B"]' in socket.sent[1]
    assert seen == [
        LatestTrade(
            ticker="BRK/B",
            price=Decimal("501.25"),
            observed_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
            source="alpaca-iex-stream",
        )
    ]
