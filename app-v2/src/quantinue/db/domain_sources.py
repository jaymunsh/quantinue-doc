"""Atomic persistence for consumed disclosure and news source records."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.db.domain_records import StrategistSignalWrite
from quantinue.db.reason import reason_payload


class _IdentifierRow(BaseModel):
    """Strict scalar row returned by PostgreSQL."""

    model_config = ConfigDict(strict=True)
    value: int


def _db_confidence(value: float) -> Decimal:
    """Serialize a binary float as its stable boundary decimal representation."""
    return Decimal(str(value))


async def save_source_records(
    engine: AsyncEngine,
    tables: tuple[Table, Table, Table, Table],
    value: StrategistSignalWrite,
    disclosure_source: DisclosureSourceRecord,
    news_source: NewsSourceRecord,
) -> None:
    """Append raw and normalized source rows without replacing prior evidence."""
    raw_disclosure, raw_news, disclosure, news = tables
    raw_disclosure_write = (
        insert(raw_disclosure)
        .values(
            ticker=value.ticker,
            trade_date=value.trade_date,
            filing_no=disclosure_source.filing_no,
            form_type=disclosure_source.form_type,
            filing_title=disclosure_source.title,
            filed_at=disclosure_source.filed_at,
            event_type=disclosure_source.event_type,
            sentiment_score=value.disclosure_score,
            importance=value.disclosure_score,
            risk_score=0,
            confidence=_db_confidence(disclosure_source.confidence),
            reason=reason_payload(),
            summary=disclosure_source.summary,
            source=disclosure_source.source,
            source_ref=disclosure_source.source_ref,
            captured_at=disclosure_source.captured_at or disclosure_source.filed_at,
            evidence_id=disclosure_source.evidence_id,
            parent_evidence_ids=list(disclosure_source.parent_evidence_ids),
            model_provider=disclosure_source.model_provider.value,
            model_name=disclosure_source.model_name,
            prompt_version=disclosure_source.prompt_version,
            policy_version=disclosure_source.policy_version,
            input_hash=disclosure_source.input_hash,
            permission="trade_eligible",
        )
        .on_conflict_do_nothing(index_elements=["filing_no"])
    )
    raw_news_write = (
        insert(raw_news)
        .values(
            ticker=value.ticker,
            trade_date=value.trade_date,
            news_key=news_source.news_key,
            title=news_source.title,
            source=news_source.source,
            url=news_source.url,
            published_at=news_source.published_at,
            grade="allow",
            is_dropped=False,
            event_type="other",
            sentiment_score=value.news_score,
            importance=value.news_score,
            risk_score=0,
            source_trust=1,
            confidence=_db_confidence(news_source.confidence),
            is_confirmed=True,
            reason=reason_payload(),
            summary=news_source.summary,
            source_ref=news_source.url,
            captured_at=news_source.captured_at or news_source.published_at,
            evidence_id=news_source.evidence_id,
            parent_evidence_ids=list(news_source.parent_evidence_ids),
            model_provider=news_source.model_provider.value,
            model_name=news_source.model_name,
            prompt_version=news_source.prompt_version,
            policy_version=news_source.policy_version,
            input_hash=news_source.input_hash,
            permission="trade_eligible",
        )
        .on_conflict_do_nothing(index_elements=["news_key", "ticker"])
    )
    disclosure_write = (
        insert(disclosure)
        .values(
            cycle_ts=value.cycle_ts,
            ticker=value.ticker,
            trade_date=value.trade_date,
            has_signal=value.disclosure_score > 0,
            filing_title=disclosure_source.title,
            filing_no=disclosure_source.filing_no,
            filed_at=disclosure_source.filed_at,
            event_type=disclosure_source.event_type,
            reason=reason_payload(),
            confidence=_db_confidence(disclosure_source.confidence),
            is_hard_blocked=False,
            source=disclosure_source.source,
            source_ref=disclosure_source.source_ref,
            captured_at=disclosure_source.captured_at or disclosure_source.filed_at,
            evidence_id=disclosure_source.evidence_id,
            parent_evidence_ids=list(disclosure_source.parent_evidence_ids),
            model_provider=disclosure_source.model_provider.value,
            model_name=disclosure_source.model_name,
            prompt_version=disclosure_source.prompt_version,
            policy_version=disclosure_source.policy_version,
            input_hash=disclosure_source.input_hash,
        )
        .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts"])
    )
    news_write = (
        insert(news)
        .values(
            cycle_ts=value.cycle_ts,
            ticker=value.ticker,
            trade_date=value.trade_date,
            has_signal=value.news_score > 0,
            news_title=news_source.title,
            source=news_source.source,
            published_at=news_source.published_at,
            ref=news_source.url,
            event_type="other",
            reason=reason_payload(),
            summary=news_source.summary,
            news_count=1,
            confidence=_db_confidence(news_source.confidence),
            is_hard_blocked=False,
            source_ref=news_source.url,
            captured_at=news_source.captured_at or news_source.published_at,
            evidence_id=news_source.evidence_id,
            parent_evidence_ids=list(news_source.parent_evidence_ids),
            model_provider=news_source.model_provider.value,
            model_name=news_source.model_name,
            prompt_version=news_source.prompt_version,
            policy_version=news_source.policy_version,
            input_hash=news_source.input_hash,
        )
        .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts"])
    )
    async with engine.begin() as connection:
        _ = await connection.execute(raw_disclosure_write)
        _ = await connection.execute(raw_news_write)
        news_id = _IdentifierRow.model_validate(
            {
                "value": await connection.scalar(
                    select(raw_news.c.id).where(
                        raw_news.c.news_key == news_source.news_key,
                        raw_news.c.ticker == value.ticker,
                    )
                )
            }
        ).value
        _ = await connection.execute(disclosure_write)
        _ = await connection.execute(news_write.values(rep_news_id=news_id))
