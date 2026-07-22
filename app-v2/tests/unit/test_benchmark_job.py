from datetime import date
from decimal import Decimal

import pytest

from quantinue.db.domain_records import DailyBarWrite
from quantinue.orchestration.benchmark_job import build_benchmark_job


class _Source:
    async def daily_bars_range(
        self, start: date, end: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        assert tickers == ("SPY",)
        return (
            DailyBarWrite(
                end, "SPY", Decimal(500), Decimal(510), Decimal(495), Decimal(505), 1, "fixture"
            ),
        )


class _Ledger:
    def __init__(self) -> None:
        self.saved: tuple[DailyBarWrite, ...] = ()

    async def benchmark_coverage(self, ticker: str) -> date | None:
        assert ticker == "SPY"
        return None

    async def save_benchmark_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        self.saved = bars


@pytest.mark.anyio
async def test_benchmark_job_backfills_spy_into_its_own_ledger() -> None:
    # Given
    ledger = _Ledger()
    job = build_benchmark_job(source=_Source(), ledger=ledger, history_days=30)

    # When
    detail = await job.run(date(2026, 7, 21))

    # Then
    assert ledger.saved[0].ticker == "SPY"
    assert detail.startswith("1 SPY closes")
