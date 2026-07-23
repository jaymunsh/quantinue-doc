from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import anyio
import pytest

from quantinue.market_data.models import LatestTrade
from quantinue.orchestration.policy import RejudgeConfig, WatchConfig
from quantinue.orchestration.watch_policy import WatchStreamConfig
from quantinue.orchestration.watch_runner import WatchOutcome, WatchRunner
from quantinue.orchestration.work_lease import WorkLease
from quantinue.roles.exits import ExitDecision, ExitReason, OpenPosition
from quantinue.runtime_status import StreamState


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
    def __init__(self) -> None:
        self.sweeps: dict[datetime, str] = {}
        self.sweep_attempts: dict[datetime, int] = {}

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return (_position(),)

    async def reference_closes(
        self, tickers: tuple[str, ...], *, before: date
    ) -> dict[str, Decimal]:
        return {ticker: Decimal("100.00") for ticker in tickers}

    async def reserve_watch_sweep(self, sweep_at: datetime, *, now: datetime) -> int | None:
        _ = now
        if self.sweeps.get(sweep_at) in {"running", "succeeded"}:
            return None
        self.sweeps[sweep_at] = "running"
        attempt = self.sweep_attempts.get(sweep_at, 0) + 1
        self.sweep_attempts[sweep_at] = attempt
        return attempt

    async def finish_watch_sweep(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        succeeded: bool,
        detail: str,
        now: datetime,
    ) -> None:
        _ = (detail, now)
        if self.sweep_attempts.get(sweep_at) != attempt:
            message = "stale sweep owner"
            raise RuntimeError(message)
        self.sweeps[sweep_at] = "succeeded" if succeeded else "failed"

    async def renew_watch_sweep(
        self, sweep_at: datetime, *, attempt: int, now: datetime
    ) -> bool:
        _ = now
        return (
            self.sweeps.get(sweep_at) == "running"
            and self.sweep_attempts.get(sweep_at) == attempt
        )


class _ManyPositionsDomain(_Domain):
    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return tuple(replace(_position(), ticker=f"TICK{index:02d}") for index in range(35))


class _EmptyDomain(_Domain):
    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return ()


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


class _Rejudge:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, dict[str, Decimal]]] = []

    async def run(
        self,
        *,
        now: datetime,
        prices: dict[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        _ = lease
        self.calls.append((now, prices))
        return 1


class _PartialRejudge(_Rejudge):
    async def run(
        self,
        *,
        now: datetime,
        prices: dict[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        _ = lease
        self.calls.append((now, prices))
        if len(self.calls) == 1:
            message = "partial analysis failure"
            raise RuntimeError(message)
        return 0


class _CancelledRejudge(_Rejudge):
    async def run(
        self,
        *,
        now: datetime,
        prices: dict[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        _ = lease
        self.calls.append((now, prices))
        raise anyio.get_cancelled_exc_class()


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


@pytest.mark.anyio
async def test_five_percent_move_rejudges_once_inside_the_cooldown() -> None:
    # Given
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "105.00")
    rejudge = _Rejudge()
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=_Domain(),
        quotes=quotes,
        exits=_NoExit(),
        rejudge=rejudge,
    )
    first = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)

    # When
    first_outcome = await runner.tick(first)
    second_outcome = await runner.tick(first.replace(minute=29))

    # Then
    assert first_outcome.rejudged == 1
    assert second_outcome.rejudged == 0
    assert rejudge.calls == [(first, {"NVDA": Decimal("105.00")})]


@pytest.mark.anyio
async def test_new_york_sweep_rejudges_a_small_move_only_once() -> None:
    # Given
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "103.00")
    rejudge = _Rejudge()
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=_Domain(),
        quotes=quotes,
        exits=_NoExit(),
        rejudge=rejudge,
    )
    sweep = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)

    # When
    first = await runner.tick(sweep)
    second = await runner.tick(sweep.replace(second=30))

    # Then
    assert first.rejudged == 1
    assert second.rejudged == 0
    assert len(rejudge.calls) == 1


@pytest.mark.anyio
async def test_missed_sweep_runs_latest_due_once_across_restart() -> None:
    # Given
    domain = _Domain()
    rejudge = _Rejudge()
    config = WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True))
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "103.00")
    now = datetime(2026, 7, 20, 16, 46, tzinfo=UTC)

    # When
    first = await WatchRunner(
        config, domain=domain, quotes=quotes, exits=_NoExit(), rejudge=rejudge
    ).tick(now)
    second = await WatchRunner(
        config, domain=domain, quotes=quotes, exits=_NoExit(), rejudge=rejudge
    ).tick(now.replace(second=30))

    # Then
    assert (first.rejudged, second.rejudged) == (1, 0)
    assert tuple(domain.sweeps) == (datetime(2026, 7, 20, 16, 45, tzinfo=UTC),)


@pytest.mark.anyio
async def test_partial_sweep_fails_then_retries_on_the_next_tick() -> None:
    # Given
    domain = _Domain()
    rejudge = _PartialRejudge()
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "103.00")
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=domain,
        quotes=quotes,
        exits=_NoExit(),
        rejudge=rejudge,
    )
    now = datetime(2026, 7, 20, 14, 1, tzinfo=UTC)

    # When / Then
    with pytest.raises(RuntimeError, match="partial"):
        await runner.tick(now)
    assert tuple(domain.sweeps.values()) == ("failed",)
    outcome = await runner.tick(now.replace(minute=2))
    assert outcome.rejudged == 0
    assert tuple(domain.sweeps.values()) == ("succeeded",)


@pytest.mark.anyio
async def test_zero_target_sweep_is_durably_succeeded() -> None:
    # Given
    domain = _EmptyDomain()
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=domain,
        quotes=_Quotes(),
        exits=_NoExit(),
        rejudge=_Rejudge(),
    )

    # When
    outcome = await runner.tick(datetime(2026, 7, 20, 14, 1, tzinfo=UTC))

    # Then
    assert outcome.rejudged == 0
    assert tuple(domain.sweeps.values()) == ("succeeded",)


@pytest.mark.anyio
async def test_cancelled_sweep_is_marked_failed_for_restart() -> None:
    # Given
    domain = _Domain()
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "103.00")
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=domain,
        quotes=quotes,
        exits=_NoExit(),
        rejudge=_CancelledRejudge(),
    )

    # When / Then
    with pytest.raises(anyio.get_cancelled_exc_class()):
        await runner.tick(datetime(2026, 7, 20, 14, 1, tzinfo=UTC))
    assert tuple(domain.sweeps.values()) == ("failed",)


@pytest.mark.anyio
async def test_sweep_uses_new_york_wall_time_after_dst_ends() -> None:
    # Given
    domain = _Domain()
    quotes = _Quotes()
    quotes.latest_trades = lambda tickers: _latest_trade(tickers, "103.00")
    runner = WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=domain,
        quotes=quotes,
        exits=_NoExit(),
        rejudge=_Rejudge(),
    )

    # When
    outcome = await runner.tick(datetime(2026, 11, 2, 15, 1, tzinfo=UTC))

    # Then
    assert outcome.rejudged == 1
    assert tuple(domain.sweeps) == (datetime(2026, 11, 2, 15, 0, tzinfo=UTC),)


@pytest.mark.anyio
async def test_stream_subscribes_to_held_tickers_within_the_free_plan_limit() -> None:
    # Given
    runner = WatchRunner(
        WatchConfig(enabled=True, stream=WatchStreamConfig(enabled=True)),
        domain=_ManyPositionsDomain(),
        quotes=_Quotes(),
        exits=_NoExit(),
    )

    # When
    tickers = await runner.stream_tickers()

    # Then
    assert tickers == tuple(f"TICK{index:02d}" for index in range(30))


@pytest.mark.anyio
async def test_stream_trade_triggers_a_protective_exit_without_waiting_for_polling() -> None:
    # Given
    exits = _Exits()
    notify = _Notify()
    runner = WatchRunner(
        WatchConfig(enabled=True, stream=WatchStreamConfig(enabled=True)),
        domain=_Domain(),
        quotes=_Quotes(),
        exits=exits,
        notifier=notify,
    )
    trade = LatestTrade(
        ticker="NVDA",
        price=Decimal("84.00"),
        observed_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        source="alpaca-iex-stream",
    )

    # When
    outcome = await runner.ingest_stream_trade(trade)

    # Then
    assert (outcome.watched, outcome.closed) == (1, 1)
    assert exits.calls == [(date(2026, 7, 20), {"NVDA": Decimal("84.00")})]
    assert len(notify.messages) == 1


@pytest.mark.anyio
async def test_stream_ignores_an_out_of_order_trade_for_the_same_ticker() -> None:
    # Given
    exits = _Exits()
    runner = WatchRunner(
        WatchConfig(enabled=True, stream=WatchStreamConfig(enabled=True)),
        domain=_Domain(),
        quotes=_Quotes(),
        exits=exits,
    )
    recent = LatestTrade(
        ticker="NVDA",
        price=Decimal("100.00"),
        observed_at=datetime(2026, 7, 20, 14, 1, tzinfo=UTC),
        source="alpaca-iex-stream",
    )
    stale = recent.model_copy(
        update={"price": Decimal("84.00"), "observed_at": recent.observed_at.replace(minute=0)}
    )
    await runner.ingest_stream_trade(recent)

    # When
    outcome = await runner.ingest_stream_trade(stale)

    # Then
    assert outcome.watched == 0
    assert exits.calls == [(date(2026, 7, 20), {"NVDA": Decimal("100.00")})]


@pytest.mark.anyio
async def test_polling_continues_while_stream_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    class _ReconnectingStream:
        state: StreamState = "reconnecting"

        async def run(
            self,
            tickers: Callable[[], Awaitable[tuple[str, ...]]],
            consume: Callable[[LatestTrade], Awaitable[None]],
        ) -> None:
            del tickers, consume
            await anyio.sleep_forever()

    runner = WatchRunner(
        WatchConfig(
            enabled=True,
            stream=WatchStreamConfig(enabled=True),
        ),
        domain=_Domain(),
        quotes=_Quotes(),
        exits=_NoExit(),
        stream=_ReconnectingStream(),
    )
    ticks = 0

    async def tick(_: datetime) -> WatchOutcome:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            raise anyio.get_cancelled_exc_class()
        return WatchOutcome("ready")

    runner.tick = tick

    async def advance_interval(_: float) -> None:
        return None

    monkeypatch.setattr("quantinue.orchestration.watch_runner.anyio.sleep", advance_interval)

    # When
    await runner.run_forever()

    # Then
    assert ticks == 2


@pytest.mark.anyio
async def test_simultaneous_stream_and_poll_prices_close_a_position_once() -> None:
    # Given
    class _IdempotentExit:
        def __init__(self) -> None:
            self.closed = False
            self.close_count = 0

        async def run_brackets(
            self, *, as_of: date, prices: Mapping[str, Decimal]
        ) -> tuple[ExitDecision, ...]:
            del as_of, prices
            if self.closed:
                return ()
            self.closed = True
            self.close_count += 1
            return ()

    exits = _IdempotentExit()
    runner = WatchRunner(
        WatchConfig(enabled=True, stream=WatchStreamConfig(enabled=True)),
        domain=_Domain(),
        quotes=_Quotes(),
        exits=exits,
    )
    trade = LatestTrade(
        ticker="NVDA",
        price=Decimal("84.00"),
        observed_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        source="alpaca-iex-stream",
    )

    async def poll() -> None:
        _ = await runner.tick(trade.observed_at)

    async def push() -> None:
        _ = await runner.ingest_stream_trade(trade)

    # When
    async with anyio.create_task_group() as task_group:
        _ = task_group.start_soon(poll)
        _ = task_group.start_soon(push)

    # Then
    assert exits.close_count == 1


async def _latest_trade(
    tickers: tuple[str, ...], price: str
) -> tuple[LatestTrade, ...]:
    return tuple(
        LatestTrade(
            ticker=ticker,
            price=Decimal(price),
            observed_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
            source="fixture",
        )
        for ticker in tickers
    )


class _NoExit:
    async def run_brackets(
        self, *, as_of: date, prices: dict[str, Decimal]
    ) -> tuple[ExitDecision, ...]:
        return ()
