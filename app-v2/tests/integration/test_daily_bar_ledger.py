"""Phase 2: the daily bar ledger and the exit observations it feeds."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from quantinue.db.domain_records import DailyBarWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 8)


def _bar(ticker: str, **overrides: object) -> DailyBarWrite:
    fields: dict[str, object] = {
        "trade_date": _DAY,
        "ticker": ticker,
        "open": Decimal("100.00"),
        "high": Decimal("110.00"),
        "low": Decimal("95.00"),
        "close": Decimal("105.00"),
        "volume": 1_000_000,
        "source": "test",
    }
    fields.update(overrides)
    return DailyBarWrite(**fields)  # pyright: ignore[reportArgumentType]


@pytest.mark.anyio
async def test_bars_are_stored_and_read_back_for_a_day() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    await store.domain.save_daily_bars((_bar("BARA"), _bar("BARB")))
    bars = await store.domain.daily_bars(_DAY, ("BARA", "BARB"))

    # Then
    assert set(bars) == {"BARA", "BARB"}
    assert bars["BARA"].high == Decimal("110.00")
    assert bars["BARA"].low == Decimal("95.00")
    await store.close()


@pytest.mark.anyio
async def test_reloading_the_same_day_does_not_duplicate_or_drift() -> None:
    """증분 적재는 같은 날을 다시 받을 수 있다 — 그때 값이 흔들리면 안 된다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_daily_bars((_bar("BARC"),))

    # When: 같은 날짜를 정정된 값으로 다시 적재
    await store.domain.save_daily_bars(
        (_bar("BARC", close=Decimal("107.00"), high=Decimal("112.00")),)
    )
    bars = await store.domain.daily_bars(_DAY, ("BARC",))

    # Then: 한 행만 남고 최신 값이 이긴다(정정 공시가 반영되어야 하므로)
    assert bars["BARC"].close == Decimal("107.00")
    assert bars["BARC"].high == Decimal("112.00")
    await store.close()


@pytest.mark.anyio
async def test_a_missing_ticker_is_absent_rather_than_invented() -> None:
    """수집 실패를 0이나 전일 값으로 채우면 청산 잡이 가짜를 근거로 판다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_daily_bars((_bar("BARD"),))

    # When
    bars = await store.domain.daily_bars(_DAY, ("BARD", "NEVERCOLLECTED"))

    # Then
    assert "BARD" in bars
    assert "NEVERCOLLECTED" not in bars
    await store.close()


@pytest.mark.anyio
async def test_bars_become_exit_observations() -> None:
    """tb_daily_bar의 소비자 — 청산 잡이 손으로 만든 관측 대신 이걸 쓴다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_daily_bars((_bar("BARE"),))

    # When
    observations = await store.domain.exit_observations(_DAY, ("BARE",))

    # Then: 고저는 브래킷 판정에, 종가는 시간 청산의 기준가에 쓰인다
    assert observations["BARE"].day_range is not None
    assert observations["BARE"].day_range.high == Decimal("110.00")
    assert observations["BARE"].day_range.low == Decimal("95.00")
    assert observations["BARE"].last_price == Decimal("105.00")
    await store.close()
