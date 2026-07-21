from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantinue.market_data.models import LatestTrade
from quantinue.orchestration.policy import WatchConfig
from quantinue.orchestration.watch_runner import WatchRunner
from quantinue.roles.exits import ExitDecision, ExitReason, OpenPosition


def _position() -> OpenPosition:
    return OpenPosition(
        order_id=1,
        signal_id=1,
        account_id=1,
        ticker="NVDA",
        quantity=2,
        entry_price=Decimal("100.00"),
        stop_price=Decimal("85.00"),
        take_profit_price=Decimal("120.00"),
        filled_on=date(2026, 7, 6),
    )


class _Domain:
    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return (_position(),)


class _Quotes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def latest_trades(self, tickers: tuple[str, ...]) -> tuple[LatestTrade, ...]:
        self.calls.append(tickers)
        return (
            LatestTrade(
                ticker="NVDA",
                price=Decimal("84.00"),
                observed_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
                source="fixture",
            ),
        )


class _Exits:
    def __init__(self) -> None:
        self.calls: list[tuple[date, dict[str, Decimal]]] = []

    async def run_brackets(
        self, *, as_of: date, prices: dict[str, Decimal]
    ) -> tuple[ExitDecision, ...]:
        self.calls.append((as_of, prices))
        return (ExitDecision(_position(), ExitReason.STOP, Decimal("85.00")),)


class _Notify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.anyio
async def test_watch_tick_is_ready_during_the_regular_session() -> None:
    # Given
    runner = WatchRunner(WatchConfig(enabled=True))

    # When
    outcome = await runner.tick(datetime(2026, 7, 20, 14, 0, tzinfo=UTC))

    # Then
    assert outcome.reason == "ready"


@pytest.mark.anyio
async def test_watch_tick_is_closed_before_the_regular_session() -> None:
    # Given
    runner = WatchRunner(WatchConfig(enabled=True))

    # When
    outcome = await runner.tick(datetime(2026, 7, 20, 12, 0, tzinfo=UTC))

    # Then
    assert outcome.reason == "market_closed"


@pytest.mark.anyio
async def test_watch_tick_is_closed_on_a_holiday() -> None:
    # Given
    runner = WatchRunner(WatchConfig(enabled=True))

    # When
    outcome = await runner.tick(datetime(2026, 7, 3, 14, 0, tzinfo=UTC))

    # Then
    assert outcome.reason == "market_closed"


@pytest.mark.anyio
async def test_disabled_watch_tick_is_completely_inert() -> None:
    # Given
    runner = WatchRunner(WatchConfig(enabled=False))

    # When
    outcome = await runner.tick(datetime(2026, 7, 20, 14, 0, tzinfo=UTC))

    # Then
    assert outcome.reason == "disabled"


@pytest.mark.anyio
async def test_open_tick_fetches_held_quotes_and_closes_in_the_same_tick() -> None:
    # Given
    quotes = _Quotes()
    exits = _Exits()
    notify = _Notify()
    runner = WatchRunner(
        WatchConfig(enabled=True),
        domain=_Domain(),
        quotes=quotes,
        exits=exits,
        notifier=notify,
    )

    # When
    outcome = await runner.tick(datetime(2026, 7, 20, 14, 0, tzinfo=UTC))

    # Then
    assert outcome.reason == "ready"
    assert (outcome.watched, outcome.closed) == (1, 1)
    assert quotes.calls == [("NVDA",)]
    assert exits.calls == [(date(2026, 7, 20), {"NVDA": Decimal("84.00")})]
    assert len(notify.messages) == 1
    assert "NVDA 2주" in notify.messages[0]
    assert "손절" in notify.messages[0]
