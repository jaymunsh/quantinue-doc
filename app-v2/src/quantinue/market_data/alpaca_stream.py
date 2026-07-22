"""Live IEX trade transport for held-position defense."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal  # noqa: TC003 - Pydantic resolves this field at runtime.
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias

import anyio
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, ValidationError
from structlog.stdlib import BoundLogger, get_logger
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from quantinue.core.schemas import AwareDateTime  # noqa: TC001
from quantinue.market_data.models import LatestTrade
from quantinue.market_data.symbols import from_venue_symbol, to_venue_symbol

if TYPE_CHECKING:
    from quantinue.orchestration.watch_policy import WatchStreamConfig

_STREAM_URL: Final = "wss://stream.data.alpaca.markets/v2/iex"
_SOURCE: Final = "alpaca-iex-stream"


class _StreamSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


StreamConnector: TypeAlias = Callable[[], AbstractAsyncContextManager[_StreamSocket]]


class _BatchWire(RootModel[list[JsonValue]]):
    pass


class _SuccessWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: Literal["success"] = Field(alias="T")
    message: str = Field(alias="msg")


class _TradeWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: Literal["t"] = Field(alias="T")
    ticker: str = Field(alias="S", min_length=1)
    price: Decimal = Field(alias="p", gt=0)
    observed_at: AwareDateTime = Field(alias="t")


@dataclass(frozen=True, slots=True)
class _StreamProtocolError(Exception):
    stage: str


@dataclass(frozen=True, slots=True)
class AlpacaTradeStream:
    """Reconnect one Basic-plan IEX session and synchronize held tickers."""

    key_id: str
    secret_key: str
    config: WatchStreamConfig
    connector: StreamConnector | None = None

    async def run(
        self,
        tickers: Callable[[], Awaitable[tuple[str, ...]]],
        consume: Callable[[LatestTrade], Awaitable[None]],
    ) -> None:
        """Reconnect until cancelled while polling remains the safety fallback."""
        logger: BoundLogger = get_logger("watch.stream")
        while True:
            try:
                connector = self.connector or self._connect
                async with connector() as socket:
                    await self._run_session(socket, tickers, consume)
            except (
                ConnectionClosed,
                InvalidHandshake,
                OSError,
                TimeoutError,
                _StreamProtocolError,
            ) as error:
                await logger.awarning("watch.stream.reconnect", error_type=type(error).__name__)
                await anyio.sleep(self.config.reconnect_seconds)

    def _connect(self) -> AbstractAsyncContextManager[_StreamSocket]:
        return connect(_STREAM_URL, open_timeout=10, ping_interval=20, ping_timeout=20)

    async def _run_session(
        self,
        socket: _StreamSocket,
        tickers: Callable[[], Awaitable[tuple[str, ...]]],
        consume: Callable[[LatestTrade], Awaitable[None]],
    ) -> None:
        self._expect_success(await socket.recv(), "connected")
        await socket.send(
            json.dumps(
                {"action": "auth", "key": self.key_id, "secret": self.secret_key},
                separators=(",", ":"),
            )
        )
        self._expect_success(await socket.recv(), "authenticated")
        subscribed = await self._sync(socket, frozenset(), await tickers())
        while True:
            message: str | bytes = b"[]"
            with anyio.move_on_after(self.config.resubscribe_seconds) as timeout:
                message = await socket.recv()
            if timeout.cancelled_caught:
                subscribed = await self._sync(socket, subscribed, await tickers())
                continue
            for trade in self._parse_trades(message):
                await consume(trade)

    async def _sync(
        self,
        socket: _StreamSocket,
        subscribed: frozenset[str],
        tickers: tuple[str, ...],
    ) -> frozenset[str]:
        desired = frozenset(to_venue_symbol(ticker) for ticker in tickers)
        removed = sorted(subscribed - desired)
        added = sorted(desired - subscribed)
        if removed:
            await socket.send(
                json.dumps({"action": "unsubscribe", "trades": removed}, separators=(",", ":"))
            )
        if added:
            await socket.send(
                json.dumps({"action": "subscribe", "trades": added}, separators=(",", ":"))
            )
        return desired

    @staticmethod
    def _expect_success(message: str | bytes, expected: str) -> None:
        try:
            batch = _BatchWire.model_validate_json(message)
            success = _SuccessWire.model_validate(batch.root[0])
        except (ValidationError, IndexError) as error:
            raise _StreamProtocolError(expected) from error
        if success.message != expected:
            raise _StreamProtocolError(expected)

    @staticmethod
    def _parse_trades(message: str | bytes) -> tuple[LatestTrade, ...]:
        try:
            batch = _BatchWire.model_validate_json(message)
        except ValidationError:
            return ()
        trades: list[LatestTrade] = []
        for item in batch.root:
            try:
                trade = _TradeWire.model_validate(item)
            except ValidationError:
                continue
            trades.append(
                LatestTrade(
                    ticker=from_venue_symbol(trade.ticker),
                    price=trade.price,
                    observed_at=trade.observed_at,
                    source=_SOURCE,
                )
            )
        return tuple(trades)
