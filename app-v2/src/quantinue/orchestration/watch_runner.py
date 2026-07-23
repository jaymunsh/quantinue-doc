"""Regular-session gate and loop for intraday position watching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

import anyio
import structlog

from quantinue.core.market_calendar import NEW_YORK, NyseCalendar
from quantinue.roles.exits.alerts import format_exit_alert
from quantinue.runtime_status import RuntimeSnapshot, StreamState, WatchRuntimeState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import date

    from quantinue.market_data.models import LatestTrade
    from quantinue.orchestration.policy import WatchConfig
    from quantinue.roles.exits import ExitDecision, OpenPosition


class WatchDomain(Protocol):
    """Portfolio reads required by one intraday watch tick."""

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        """Return every position that remains open."""
        ...

    async def reference_closes(
        self, tickers: tuple[str, ...], *, before: date
    ) -> Mapping[str, Decimal]:
        """Return the last closed-session price before the intraday tick."""
        ...


class LatestTradeSource(Protocol):
    """Batch latest-trade capability consumed by the watch runner."""

    async def latest_trades(self, tickers: tuple[str, ...]) -> tuple[LatestTrade, ...]:
        """Return one recent trade for each symbol the source observed."""
        ...


class LiveTradeStream(Protocol):
    """Push normalized held-position trades into the watch runner."""

    async def run(
        self,
        tickers: Callable[[], Awaitable[tuple[str, ...]]],
        consume: Callable[[LatestTrade], Awaitable[None]],
    ) -> None:
        """Keep subscriptions synchronized until application cancellation."""
        ...

    @property
    def state(self) -> StreamState:
        """Return the current transport lifecycle state."""
        ...


class BracketExitExecutor(Protocol):
    """Execute triggered protective legs through the durable exit path."""

    async def run_brackets(
        self, *, as_of: date, prices: Mapping[str, Decimal]
    ) -> tuple[ExitDecision, ...]:
        """Execute every protective leg reached by the supplied prices."""
        ...


class RejudgeExecutor(Protocol):
    """Re-run the shared proposal and critic path for triggered holdings."""

    async def run(self, *, now: datetime, prices: Mapping[str, Decimal]) -> int:
        """Return how many holdings the refreshed judgement closed."""
        ...


@dataclass(frozen=True, slots=True)
class WatchOutcome:
    """One observable result from an intraday watch tick."""

    reason: Literal["disabled", "market_closed", "ready"]
    watched: int = 0
    closed: int = 0
    rejudged: int = 0


class WatchRunner:
    """Wake during the regular session without touching the daily-job ledger."""

    def __init__(  # noqa: PLR0913 - 각 인자는 독립된 런타임 협력자다.
        self,
        config: WatchConfig,
        calendar: NyseCalendar | None = None,
        domain: WatchDomain | None = None,
        quotes: LatestTradeSource | None = None,
        exits: BracketExitExecutor | None = None,
        notifier: Callable[[str], Awaitable[None]] | None = None,
        rejudge: RejudgeExecutor | None = None,
        stream: LiveTradeStream | None = None,
    ) -> None:
        """Bind the watch policy to the shared NYSE calendar adapter."""
        self._config = config
        self._calendar = calendar or NyseCalendar()
        self._domain = domain
        self._quotes = quotes
        self._exits = exits
        self._notifier = notifier
        self._rejudge = rejudge
        self._stream = stream
        self._last_rejudged_at: dict[str, datetime] = {}
        self._last_stream_at: dict[str, datetime] = {}
        self._completed_sweeps: set[tuple[date, str]] = set()
        self._evaluation_lock = anyio.Lock()
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("watch")
        self._runtime = WatchRuntimeState(
            rejudge_configured=config.rejudge.enabled,
            stream_configured=config.stream.enabled,
        )
        self._stream_state: StreamState = "off"

    def snapshot(self) -> RuntimeSnapshot:
        """Return an immutable copy of current runner liveness."""
        stream_state = (
            self._stream_state
            if self._stream is None
            else self._stream.state
        )
        return self._runtime.snapshot(stream_state=stream_state)

    async def tick(self, now: datetime) -> WatchOutcome:
        """Run one polling boundary and record its observable result."""
        try:
            outcome = await self._tick(now)
        except Exception:
            self._runtime.record(now, "failed")
            raise
        self._runtime.record(now, outcome.reason)
        return outcome

    async def _tick(self, now: datetime) -> WatchOutcome:
        if not self._config.enabled:
            return WatchOutcome("disabled")
        if not self._calendar.is_market_open(now):
            return WatchOutcome("market_closed")
        if self._domain is None or self._quotes is None or self._exits is None:
            return WatchOutcome("ready")
        positions = await self._domain.open_positions()
        as_of = now.astimezone(NEW_YORK).date()
        candidate_reader = getattr(self._domain, "watch_tickers", None)
        candidates = () if candidate_reader is None else await candidate_reader(as_of)
        tickers = tuple(
            dict.fromkeys(
                (*candidates, *(position.ticker for position in positions))
            )
        )
        if not tickers:
            return WatchOutcome("ready")
        trades = await self._quotes.latest_trades(tickers)
        prices = {trade.ticker: trade.price for trade in trades}
        async with self._evaluation_lock:
            return await self._evaluate(now, tickers=tickers, prices=prices)

    async def stream_tickers(self) -> tuple[str, ...]:
        """Prefer held positions and stay inside the configured stream plan."""
        if self._domain is None:
            return ()
        positions = await self._domain.open_positions()
        return tuple(dict.fromkeys(position.ticker for position in positions))[
            : self._config.stream.symbol_limit
        ]

    async def ingest_stream_trade(self, trade: LatestTrade) -> WatchOutcome:
        """Evaluate one fresh held-position trade without waiting for polling."""
        if not self._config.enabled or not self._config.stream.enabled:
            return WatchOutcome("disabled")
        if not self._calendar.is_market_open(trade.observed_at):
            return WatchOutcome("market_closed")
        if self._domain is None or self._exits is None:
            return WatchOutcome("ready")
        async with self._evaluation_lock:
            previous = self._last_stream_at.get(trade.ticker)
            if previous is not None and trade.observed_at <= previous:
                return WatchOutcome("ready")
            positions = await self._domain.open_positions()
            if trade.ticker not in {position.ticker for position in positions}:
                return WatchOutcome("ready")
            self._last_stream_at[trade.ticker] = trade.observed_at
            return await self._evaluate(
                trade.observed_at,
                tickers=(trade.ticker,),
                prices={trade.ticker: trade.price},
            )

    @property
    def has_live_stream(self) -> bool:
        """Report whether the runner owns an event-driven price source."""
        return self._stream is not None

    async def _consume_stream_trade(self, trade: LatestTrade) -> None:
        _ = await self.ingest_stream_trade(trade)

    async def _evaluate(
        self,
        now: datetime,
        *,
        tickers: tuple[str, ...],
        prices: Mapping[str, Decimal],
    ) -> WatchOutcome:
        exits = self._exits
        if exits is None:
            return WatchOutcome("ready")
        as_of = now.astimezone(NEW_YORK).date()
        closed = await exits.run_brackets(as_of=as_of, prices=prices)
        if closed and self._notifier is not None:
            await self._notifier(format_exit_alert(as_of, closed))
        surviving = tuple(
            ticker
            for ticker in tickers
            if ticker not in {decision.position.ticker for decision in closed}
        )
        rejudged = await self._rejudge_moves(now, tickers=surviving, prices=prices)
        return WatchOutcome(
            "ready", watched=len(tickers), closed=len(closed) + rejudged, rejudged=rejudged
        )

    async def _rejudge_moves(
        self,
        now: datetime,
        *,
        tickers: tuple[str, ...],
        prices: Mapping[str, Decimal],
    ) -> int:
        """Send material, cooled-down price moves to the shared LLM path."""
        policy = self._config.rejudge
        if not policy.enabled or self._rejudge is None or self._domain is None:
            return 0
        active = tuple(dict.fromkeys(tickers))
        if not active:
            return 0
        references = await self._domain.reference_closes(
            active, before=now.astimezone(NEW_YORK).date()
        )
        local = now.astimezone(NEW_YORK)
        sweep_time = local.strftime("%H:%M")
        sweep_key = (local.date(), sweep_time)
        sweep_due = (
            sweep_time in policy.sweep_times_ny
            and sweep_key not in self._completed_sweeps
        )
        cooldown = timedelta(minutes=policy.cooldown_minutes)
        triggered: dict[str, Decimal] = {}
        for ticker in active:
            price = prices.get(ticker)
            reference = references.get(ticker)
            if price is None or reference is None or reference <= 0:
                continue
            moved = abs(price - reference) / reference >= Decimal(
                str(policy.move_trigger_pct)
            )
            if not moved and not sweep_due:
                continue
            previous = self._last_rejudged_at.get(ticker)
            if previous is None or now - previous >= cooldown:
                triggered[ticker] = price
        if not triggered:
            return 0
        closed = await self._rejudge.run(now=now, prices=triggered)
        self._last_rejudged_at.update(dict.fromkeys(triggered, now))
        if sweep_due:
            self._completed_sweeps.add(sweep_key)
        return closed

    async def run_forever(self) -> None:
        """Tick forever while isolating failures from the application lifespan."""
        if self._config.stream.enabled and self._stream is not None:
            async with anyio.create_task_group() as task_group:
                _ = task_group.start_soon(self._poll_forever)
                _ = task_group.start_soon(
                    self._stream.run, self.stream_tickers, self._consume_stream_trade
                )
            return
        await self._poll_forever()

    async def _poll_forever(self) -> None:
        while True:
            attempted_at = datetime.now(UTC)
            try:
                outcome = await self.tick(attempted_at)
                if outcome.reason == "ready":
                    await self._logger.ainfo("watch.tick", reason=outcome.reason)
            except Exception:  # noqa: BLE001 - 한 틱 실패가 다음 감시 기회를 없애면 안 된다.
                await self._logger.aexception("watch.tick.failed")
            await anyio.sleep(self._config.interval_minutes * 60)
