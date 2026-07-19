"""Phase 2: the raw disclosure ledger and the hard events it feeds to exits."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from quantinue.db.domain_records import DailyBarWrite, RawDisclosureWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 15)


def _filing(ticker: str, *, hard: bool, suffix: str) -> RawDisclosureWrite:
    return RawDisclosureWrite(
        filing_no=f"0001354457-26-{suffix}",
        trade_date=_DAY,
        ticker=ticker,
        cik="1369568",
        form_type="25-NSE" if hard else "8-K",
        company_name=f"{ticker} Inc",
        source_ref=f"edgar/data/1369568/0001354457-26-{suffix}.txt",
        event_type="delisting_halt" if hard else None,
        is_hard_event=hard,
    )


def _bar(ticker: str) -> DailyBarWrite:
    return DailyBarWrite(
        trade_date=_DAY,
        ticker=ticker,
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(95),
        close=Decimal(105),
        volume=1_000,
        source="test",
    )


@pytest.mark.anyio
async def test_filings_are_stored_without_needing_the_ticker_to_be_a_daily_pick() -> None:
    """일괄 수집의 존재 이유 — 스크리너에서 탈락한 보유 종목을 덮는 것."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When: 그날 tb_daily_pick에 전혀 없는 종목
    await store.domain.save_raw_disclosures(
        (_filing("RAWNOPICK", hard=True, suffix="900001"),)
    )
    hard = await store.domain.hard_event_tickers(_DAY)

    # Then
    assert "RAWNOPICK" in hard
    await store.close()


@pytest.mark.anyio
async def test_reloading_the_same_day_does_not_duplicate() -> None:
    """수집은 재시도될 수 있다 — 접수번호가 행을 고정한다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    filing = _filing("RAWDUP", hard=True, suffix="900002")

    # When
    await store.domain.save_raw_disclosures((filing,))
    await store.domain.save_raw_disclosures((filing,))
    hard = await store.domain.hard_event_tickers(_DAY)

    # Then
    assert sorted(hard).count("RAWDUP") == 1
    await store.close()


@pytest.mark.anyio
async def test_ordinary_filings_are_not_hard_events() -> None:
    """평범한 공시를 하드로 올리면 시스템이 아무 때나 판다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    await store.domain.save_raw_disclosures(
        (_filing("RAWSOFT", hard=False, suffix="900003"),)
    )
    hard = await store.domain.hard_event_tickers(_DAY)

    # Then
    assert "RAWSOFT" not in hard
    await store.close()


@pytest.mark.anyio
async def test_a_hard_event_reaches_the_exit_observation() -> None:
    """원장에 앉은 공시가 청산 판정의 입력이 되는 지점."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_daily_bars((_bar("RAWOBS"),))
    await store.domain.save_raw_disclosures(
        (_filing("RAWOBS", hard=True, suffix="900004"),)
    )

    # When
    observations = await store.domain.exit_observations(_DAY, ("RAWOBS",))

    # Then
    assert observations["RAWOBS"].has_hard_event is True
    assert observations["RAWOBS"].last_price == Decimal(105)
    await store.close()


@pytest.mark.anyio
async def test_a_halted_ticker_with_no_bar_still_produces_an_observation() -> None:
    """거래정지되면 봉이 안 찍힌다 — 봉 기준으로만 관측을 만들면 정확히
    상장폐지 케이스가 조용히 사라진다. 팔아야 할 바로 그 종목이다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_raw_disclosures(
        (_filing("RAWHALT", hard=True, suffix="900005"),)
    )

    # When: 봉은 없고 하드 이벤트만 있다
    observations = await store.domain.exit_observations(_DAY, ("RAWHALT",))

    # Then
    assert "RAWHALT" in observations
    assert observations["RAWHALT"].has_hard_event is True
    assert observations["RAWHALT"].day_range is None
    assert observations["RAWHALT"].last_price is None
    await store.close()


@pytest.mark.anyio
async def test_a_ticker_with_neither_bar_nor_event_stays_absent() -> None:
    """수집 실패를 관측으로 둔갑시키지 않는다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    observations = await store.domain.exit_observations(_DAY, ("RAWNOTHING",))

    # Then
    assert observations == {}
    await store.close()
