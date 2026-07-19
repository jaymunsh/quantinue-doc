"""PostgreSQL proof for complete source collection persistence."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.core.ontology import ModelProvider
from quantinue.db.domain_records import SourceRecordsWrite, StrategistSignalWrite
from quantinue.db.postgres import PostgresRunStore
from quantinue.market_data.models import NewsMatchReason, NewsMatchStatus

_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
_INT = TypeAdapter(int)


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_persists_every_source_and_real_news_count() -> None:
    # Given
    assert _URL is not None
    cycle = datetime(2034, 1, 3, 13, 0, tzinfo=UTC)
    ticker = "ALLSRC"
    store = PostgresRunStore(_URL)
    await store.initialize()
    signal = StrategistSignalWrite(
        run_id="all-source-run",
        trade_date=cycle.date(),
        ticker=ticker,
        cycle_ts=cycle,
        side="hold",
        conviction=Decimal("0.4"),
        summary="collection persistence",
        decision_close=Decimal(100),
        evidence=("all-source-run:07:strategy",),
        disclosure_score=Decimal("0.6"),
        news_score=Decimal("0.7"),
    )
    disclosures = tuple(
        DisclosureSourceRecord(
            filing_no=f"all-source-filing-{index}",
            title=f"filing-{index}",
            form_type="8-K",
            filed_at=cycle,
            event_type="other",
            source_ref=f"sec://all-source/{index}",
            summary=f"filing summary {index}",
            captured_at=cycle,
            evidence_id=f"all-source-run:05:disclosure:{index}",
            model_provider=ModelProvider.MOCK,
        )
        for index in range(2)
    )
    news = tuple(
        NewsSourceRecord(
            news_key=f"all-source-news-{index}",
            title=f"news-{index}",
            url=f"https://example.invalid/all-source/{index}",
            source="rss",
            published_at=cycle,
            summary=f"news summary {index}",
            captured_at=cycle,
            evidence_id=f"all-source-run:06:news:{index}",
            model_provider=ModelProvider.MOCK,
            selection_status=status,
            relevance_score=score,
            relevance_reasons=(NewsMatchReason.TICKER_TITLE,),
            canonical_identity=f"all-source-news-{index}",
        )
        for index, (status, score) in enumerate(
            (
                (NewsMatchStatus.SELECTED, 50),
                (NewsMatchStatus.RELEVANT, 40),
                (NewsMatchStatus.EXCLUDED, 0),
            )
        )
    )
    write = SourceRecordsWrite(
        signal=signal,
        disclosures=disclosures,
        news=news,
        representative_disclosure=disclosures[0],
        representative_news=news[0],
    )
    engine = create_async_engine(_URL)
    try:
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:trade_date,:ticker,'All Sources',1)"""
                ),
                {"trade_date": cycle.date(), "ticker": ticker},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score
                    ) VALUES (:trade_date,:ticker,:trade_date,'backfill',1,'test',1)"""
                ),
                {"trade_date": cycle.date(), "ticker": ticker},
            )

        # When
        await store.domain.save_source_records(write)

        # Then
        async with engine.connect() as connection:
            disclosure_count = _INT.validate_python(
                await connection.scalar(
                    text("SELECT count(*) FROM tb_disclosure WHERE ticker=:ticker"),
                    {"ticker": ticker},
                )
            )
            news_count = _INT.validate_python(
                await connection.scalar(
                    text("SELECT count(*) FROM tb_news WHERE ticker=:ticker"),
                    {"ticker": ticker},
                )
            )
            recorded_news_count = _INT.validate_python(
                await connection.scalar(
                    text(
                        """SELECT news_count FROM tb_news_signal
                        WHERE ticker=:ticker AND cycle_ts=:cycle"""
                    ),
                    {"ticker": ticker, "cycle": cycle},
                )
            )
        assert disclosure_count == 2
        assert news_count == 3
        assert recorded_news_count == 2
    finally:
        await store.close()
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_persists_excluded_news_with_zero_signal_count() -> None:
    assert _URL is not None
    cycle = datetime(2034, 1, 4, 13, 0, tzinfo=UTC)
    ticker = "NONEWS"
    store = PostgresRunStore(_URL)
    await store.initialize()
    disclosure = DisclosureSourceRecord(
        filing_no="no-news-filing",
        title="filing",
        form_type="10-Q",
        filed_at=cycle,
        event_type="other",
        source_ref="sec://no-news",
        summary="filing summary",
        captured_at=cycle,
        evidence_id="no-news-run:05:disclosure",
        model_provider=ModelProvider.MOCK,
    )
    excluded = NewsSourceRecord(
        news_key="unrelated-news",
        title="unrelated",
        url="https://example.invalid/unrelated",
        source="rss",
        published_at=cycle,
        summary="unrelated summary",
        captured_at=cycle,
        evidence_id="no-news-run:06:news:0",
        model_provider=ModelProvider.MOCK,
        selection_status=NewsMatchStatus.EXCLUDED,
        relevance_score=0,
        canonical_identity="unrelated-news",
    )
    signal = StrategistSignalWrite(
        run_id="no-news-run",
        trade_date=cycle.date(),
        ticker=ticker,
        cycle_ts=cycle,
        side="hold",
        conviction=Decimal(0),
        summary="no related news",
        decision_close=Decimal(100),
        evidence=("no-news-run:07:strategy",),
        disclosure_score=Decimal("0.5"),
        news_score=Decimal(0),
    )
    engine = create_async_engine(_URL)
    try:
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:trade_date,:ticker,'No Related News',1)"""
                ),
                {"trade_date": cycle.date(), "ticker": ticker},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score
                    ) VALUES (:trade_date,:ticker,:trade_date,'backfill',1,'test',1)"""
                ),
                {"trade_date": cycle.date(), "ticker": ticker},
            )
        await store.domain.save_source_records(
            SourceRecordsWrite(
                signal=signal,
                disclosures=(disclosure,),
                news=(excluded,),
                representative_disclosure=disclosure,
                representative_news=None,
            )
        )
        async with engine.connect() as connection:
            raw_count = _INT.validate_python(
                await connection.scalar(
                    text("SELECT count(*) FROM tb_news WHERE ticker=:ticker"),
                    {"ticker": ticker},
                )
            )
            signal_count = _INT.validate_python(
                await connection.scalar(
                    text("SELECT news_count FROM tb_news_signal WHERE ticker=:ticker"),
                    {"ticker": ticker},
                )
            )
        assert raw_count == 1
        assert signal_count == 0
    finally:
        await store.close()
        await engine.dispose()
