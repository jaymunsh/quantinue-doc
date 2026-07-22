"""Daily SPY close collection for owner-visible relative performance."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Protocol

from quantinue.core.market_calendar import NyseCalendar
from quantinue.orchestration.job_runner import JobDefinition

if TYPE_CHECKING:
    from quantinue.db.domain_records import DailyBarWrite

_SPY: Final = "SPY"


class BenchmarkBarSource(Protocol):
    """Daily-bar capability required by the benchmark job."""

    async def daily_bars_range(
        self, start: date, end: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        """Fetch an inclusive bar window."""
        ...


class BenchmarkLedger(Protocol):
    """Persistence capability required by the benchmark job."""

    async def benchmark_coverage(self, ticker: str) -> date | None:
        """Return the newest persisted date."""
        ...

    async def save_benchmark_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        """Upsert normalized daily bars as benchmark closes."""
        ...


def build_benchmark_job(
    *,
    source: BenchmarkBarSource,
    ledger: BenchmarkLedger,
    history_days: int,
    calendar: NyseCalendar | None = None,
    name: str = "benchmark_spy",
) -> JobDefinition:
    """Backfill once, then advance the SPY close ledger incrementally."""
    market_calendar = calendar or NyseCalendar()

    async def run(as_of: date) -> str:
        session = market_calendar.previous_trading_day(as_of)
        covered = await ledger.benchmark_coverage(_SPY)
        start = (
            session - timedelta(days=history_days)
            if covered is None
            else covered + timedelta(days=1)
        )
        bars = () if start > session else await source.daily_bars_range(start, session, (_SPY,))
        await ledger.save_benchmark_bars(bars)
        return f"{len(bars)} SPY closes up to {session.isoformat()}"

    return JobDefinition(name=name, run=run)
