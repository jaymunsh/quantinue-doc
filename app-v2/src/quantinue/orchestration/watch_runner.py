"""Regular-session gate and loop for intraday position watching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

import anyio
import structlog

from quantinue.core.market_calendar import NEW_YORK, NyseCalendar
from quantinue.roles.exits.alerts import format_exit_alert

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import date
    from decimal import Decimal

    from quantinue.market_data.models import LatestTrade
    from quantinue.orchestration.policy import WatchConfig
    from quantinue.roles.exits import ExitDecision, OpenPosition


class WatchDomain(Protocol):
    """Portfolio reads required by one intraday watch tick."""

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        """Return every position that remains open."""
        ...


class LatestTradeSource(Protocol):
    """Batch latest-trade capability consumed by the watch runner."""

    async def latest_trades(self, tickers: tuple[str, ...]) -> tuple[LatestTrade, ...]:
        """Return one recent trade for each symbol the source observed."""
        ...


class BracketExitExecutor(Protocol):
    """Execute triggered protective legs through the durable exit path."""

    async def run_brackets(
        self, *, as_of: date, prices: Mapping[str, Decimal]
    ) -> tuple[ExitDecision, ...]:
        """Execute every protective leg reached by the supplied prices."""
        ...


@dataclass(frozen=True, slots=True)
class WatchOutcome:
    """One observable result from an intraday watch tick."""

    reason: Literal["disabled", "market_closed", "ready"]
    watched: int = 0
    closed: int = 0


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
    ) -> None:
        """Bind the watch policy to the shared NYSE calendar adapter."""
        self._config = config
        self._calendar = calendar or NyseCalendar()
        self._domain = domain
        self._quotes = quotes
        self._exits = exits
        self._notifier = notifier
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("watch")

    async def tick(self, now: datetime) -> WatchOutcome:
        """Apply the switch and session gate; later milestones add watch work."""
        if not self._config.enabled:
            return WatchOutcome("disabled")
        if not self._calendar.is_market_open(now):
            return WatchOutcome("market_closed")
        if self._domain is None or self._quotes is None or self._exits is None:
            return WatchOutcome("ready")
        positions = await self._domain.open_positions()
        tickers = tuple(dict.fromkeys(position.ticker for position in positions))
        if not tickers:
            return WatchOutcome("ready")
        trades = await self._quotes.latest_trades(tickers)
        prices = {trade.ticker: trade.price for trade in trades}
        as_of = now.astimezone(NEW_YORK).date()
        closed = await self._exits.run_brackets(as_of=as_of, prices=prices)
        if closed and self._notifier is not None:
            await self._notifier(format_exit_alert(as_of, closed))
        return WatchOutcome("ready", watched=len(tickers), closed=len(closed))

    async def run_forever(self) -> None:
        """Tick forever while isolating failures from the application lifespan."""
        while True:
            try:
                outcome = await self.tick(datetime.now(UTC))
                if outcome.reason == "ready":
                    await self._logger.ainfo("watch.tick", reason=outcome.reason)
            except Exception:  # noqa: BLE001 - 한 틱 실패가 다음 감시 기회를 없애면 안 된다.
                await self._logger.aexception("watch.tick.failed")
            await anyio.sleep(self._config.interval_minutes * 60)
