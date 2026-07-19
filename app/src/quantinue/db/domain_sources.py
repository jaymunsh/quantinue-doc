"""Atomic persistence for complete disclosure and news collections."""

from decimal import Decimal
from typing import assert_never

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.dml import Insert

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.db.domain_records import SourceRecordsWrite
from quantinue.market_data.models import NewsMatchStatus


class _IdentifierRow(BaseModel):
    """Strict scalar row returned by PostgreSQL."""

    model_config = ConfigDict(strict=True)
    value: int


def _db_decimal(value: float | Decimal) -> Decimal:
    """Serialize numeric values at the PostgreSQL boundary."""
    return Decimal(str(value))


def _news_state(value: NewsSourceRecord) -> tuple[str, bool, str, str]:
    """Map selection state to durable grade, drop, reason, and permission."""
    reason = (
        ",".join(item.value for item in value.relevance_reasons) or value.selection_status.value
    )
    match value.selection_status:
        case NewsMatchStatus.SELECTED | NewsMatchStatus.RELEVANT:
            return "allow", False, reason, "trade_eligible"
        case NewsMatchStatus.EXCLUDED:
            return "block", True, reason, "block"
        case NewsMatchStatus.FETCHED:
            return "gray", False, reason, "block_buy"
        case unreachable:
            assert_never(unreachable)


def _raw_disclosure_write(
    table: Table,
    write: SourceRecordsWrite,
    source: DisclosureSourceRecord,
) -> Insert:
    """Build one append-only disclosure insert."""
    is_representative = source.filing_no == write.representative_disclosure.filing_no
    sentiment = write.signal.disclosure_score if is_representative else Decimal("0.5")
    importance = write.signal.disclosure_score if is_representative else Decimal(0)
    return (
        insert(table)
        .values(
            ticker=write.signal.ticker,
            trade_date=write.signal.trade_date,
            filing_no=source.filing_no,
            form_type=source.form_type,
            filing_title=source.title,
            filed_at=source.filed_at,
            event_type=source.event_type,
            sentiment_score=sentiment,
            importance=importance,
            risk_score=0,
            confidence=_db_decimal(source.confidence),
            reason="representative for role 05" if is_representative else "collected by role 05",
            summary=source.summary,
            source=source.source,
            source_ref=source.source_ref,
            captured_at=source.captured_at or source.filed_at,
            evidence_id=source.evidence_id,
            parent_evidence_ids=list(source.parent_evidence_ids),
            model_provider=source.model_provider.value,
            model_name=source.model_name,
            prompt_version=source.prompt_version,
            policy_version=source.policy_version,
            input_hash=source.input_hash,
            permission="trade_eligible" if is_representative else "block_buy",
        )
        .on_conflict_do_nothing(index_elements=["filing_no"])
    )


def _raw_news_write(table: Table, write: SourceRecordsWrite, source: NewsSourceRecord) -> Insert:
    """Build one append-only news insert with its selection outcome."""
    grade, is_dropped, reason, permission = _news_state(source)
    is_representative = (
        write.representative_news is not None
        and source.news_key == write.representative_news.news_key
    )
    score = write.signal.news_score if is_representative else None
    return (
        insert(table)
        .values(
            ticker=write.signal.ticker,
            trade_date=write.signal.trade_date,
            news_key=source.news_key,
            title=source.title,
            source=source.source,
            url=source.url,
            published_at=source.published_at,
            grade=grade,
            is_dropped=is_dropped,
            drop_reason=reason if is_dropped else None,
            event_type="other",
            sentiment_score=score,
            importance=score,
            risk_score=0 if is_representative else None,
            source_trust=_db_decimal(source.confidence),
            confidence=_db_decimal(source.confidence),
            is_confirmed=grade == "allow",
            reason=reason,
            summary=source.summary,
            source_ref=source.url,
            captured_at=source.captured_at or source.published_at,
            evidence_id=source.evidence_id,
            parent_evidence_ids=list(source.parent_evidence_ids),
            model_provider=source.model_provider.value,
            model_name=source.model_name,
            prompt_version=source.prompt_version,
            policy_version=source.policy_version,
            input_hash=source.input_hash,
            permission=permission,
        )
        .on_conflict_do_nothing(index_elements=["news_key", "ticker"])
    )


def _disclosure_signal_write(table: Table, write: SourceRecordsWrite) -> Insert:
    """Build the per-ticker disclosure signal linked to its representative."""
    source = write.representative_disclosure
    return (
        insert(table)
        .values(
            cycle_ts=write.signal.cycle_ts,
            ticker=write.signal.ticker,
            trade_date=write.signal.trade_date,
            has_signal=write.signal.disclosure_score > 0,
            filing_title=source.title,
            filing_no=source.filing_no,
            filed_at=source.filed_at,
            event_type=source.event_type,
            sentiment_score=write.signal.disclosure_score,
            importance=write.signal.disclosure_score,
            risk_score=0,
            reason="pipeline aggregate disclosure score",
            summary=source.summary,
            confidence=_db_decimal(source.confidence),
            is_hard_blocked=False,
            source=source.source,
            source_ref=source.source_ref,
            captured_at=source.captured_at or source.filed_at,
            evidence_id=source.evidence_id,
            parent_evidence_ids=list(source.parent_evidence_ids),
            model_provider=source.model_provider.value,
            model_name=source.model_name,
            prompt_version=source.prompt_version,
            policy_version=source.policy_version,
            input_hash=source.input_hash,
        )
        .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts"])
    )


def _news_signal_write(table: Table, write: SourceRecordsWrite, news_id: int | None) -> Insert:
    """Build the per-ticker news aggregate from all relevant source rows."""
    source = write.representative_news or write.news[0]
    relevant = tuple(
        item
        for item in write.news
        if item.selection_status in {NewsMatchStatus.SELECTED, NewsMatchStatus.RELEVANT}
    )
    relevant_count = len(relevant)
    fetched_count = len(write.news)
    top_evidence = [item.evidence_id for item in relevant if item.evidence_id]
    return (
        insert(table)
        .values(
            cycle_ts=write.signal.cycle_ts,
            ticker=write.signal.ticker,
            trade_date=write.signal.trade_date,
            has_signal=write.representative_news is not None and write.signal.news_score > 0,
            rep_news_id=news_id,
            news_title=source.title,
            source=source.source,
            published_at=source.published_at,
            ref=source.url,
            event_type="other",
            disclosure_ref=write.representative_disclosure.filing_no,
            reason="pipeline aggregate from relevant news",
            summary=source.summary,
            news_count=relevant_count,
            importance=write.signal.news_score,
            peak_importance=write.signal.news_score,
            risk_score=0,
            sentiment_score=write.signal.news_score,
            source_trust=max(_db_decimal(item.confidence) for item in relevant or (source,)),
            grade_score=_db_decimal(relevant_count / fetched_count)
            if fetched_count
            else Decimal(0),
            confidence=_db_decimal(source.confidence),
            is_hard_blocked=False,
            top_evidence=top_evidence,
            source_ref=source.url,
            captured_at=source.captured_at or source.published_at,
            evidence_id=source.evidence_id,
            parent_evidence_ids=list(source.parent_evidence_ids),
            model_provider=source.model_provider.value,
            model_name=source.model_name,
            prompt_version=source.prompt_version,
            policy_version=source.policy_version,
            input_hash=source.input_hash,
        )
        .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts"])
    )


async def save_source_records(
    engine: AsyncEngine,
    tables: tuple[Table, Table, Table, Table],
    write: SourceRecordsWrite,
) -> None:
    """Persist every raw source and one aggregate signal per ticker and cycle."""
    raw_disclosure, raw_news, disclosure, news = tables
    async with engine.begin() as connection:
        for source in write.disclosures:
            _ = await connection.execute(_raw_disclosure_write(raw_disclosure, write, source))
        for source in write.news:
            _ = await connection.execute(_raw_news_write(raw_news, write, source))
        news_id: int | None = None
        if write.representative_news is not None:
            news_id = _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        select(raw_news.c.id).where(
                            raw_news.c.news_key == write.representative_news.news_key,
                            raw_news.c.ticker == write.signal.ticker,
                        )
                    )
                }
            ).value
        _ = await connection.execute(_disclosure_signal_write(disclosure, write))
        if write.news:
            _ = await connection.execute(_news_signal_write(news, write, news_id))
