"""Phase 3: the raw news ledger and the headlines it hands to the analysis job."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest

from quantinue.db.domain_records import RawNewsWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 15)


def _article(
    article_id: int, ticker: str, headline: str = "something happened", minute: int = 0
) -> RawNewsWrite:
    return RawNewsWrite(
        article_id=article_id,
        ticker=ticker,
        trade_date=_DAY,
        headline=headline,
        source="benzinga",
        url=f"https://www.benzinga.com/news/{article_id}",
        published_at=datetime(2026, 7, 15, 14, minute, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_headlines_are_stored_without_needing_the_ticker_to_be_a_daily_pick() -> None:
    """공시 원장과 같은 이유로 FK가 없다 — 범위 밖 보유 종목을 덮어야 한다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When: 그날 tb_daily_pick에 전혀 없는 종목
    await store.domain.save_raw_news((_article(9_000_001, "NEWSNOPICK"),))
    found = await store.domain.news_evidence(_DAY, ("NEWSNOPICK",), 5)

    # Then
    assert found["NEWSNOPICK"] == ("something happened",)
    await store.close()


@pytest.mark.anyio
async def test_one_article_can_be_evidence_for_several_tickers() -> None:
    """기사 하나가 여러 종목을 언급한다 — 기사 id만으로는 행을 못 가른다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    await store.domain.save_raw_news(
        (_article(9_000_002, "NEWSMULTIA"), _article(9_000_002, "NEWSMULTIB"))
    )
    found = await store.domain.news_evidence(
        _DAY, ("NEWSMULTIA", "NEWSMULTIB"), 5
    )

    # Then
    assert set(found) == {"NEWSMULTIA", "NEWSMULTIB"}
    await store.close()


@pytest.mark.anyio
async def test_recollecting_the_same_window_does_not_duplicate() -> None:
    """창이 겹치게 요청된다(세션 → 실행일) — 겹침이 원장을 부풀리면 안 된다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    article = _article(9_000_003, "NEWSDUP")

    # When
    await store.domain.save_raw_news((article,))
    await store.domain.save_raw_news((article,))
    found = await store.domain.news_evidence(_DAY, ("NEWSDUP",), 5)

    # Then
    assert found["NEWSDUP"] == ("something happened",)
    await store.close()


@pytest.mark.anyio
async def test_the_newest_headlines_are_the_ones_that_fit_the_budget() -> None:
    """예산이 종목당 N건이면 잘리는 것은 오래된 쪽이어야 한다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_raw_news(
        (
            _article(9_000_004, "NEWSCAP", headline="oldest", minute=1),
            _article(9_000_005, "NEWSCAP", headline="middle", minute=2),
            _article(9_000_006, "NEWSCAP", headline="newest", minute=3),
        )
    )

    # When
    found = await store.domain.news_evidence(_DAY, ("NEWSCAP",), 2)

    # Then
    assert found["NEWSCAP"] == ("newest", "middle")
    await store.close()


@pytest.mark.anyio
async def test_a_ticker_with_no_headlines_is_absent_rather_than_empty() -> None:
    """수집이 실패한 날과 조용한 날을 원장 수준에서 구분하지 않는다 —
    둘 다 "없음"이고, 프롬프트가 그것을 명시적으로 적는다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    found = await store.domain.news_evidence(_DAY, ("NEWSSILENT",), 5)

    # Then
    assert found == {}
    await store.close()
