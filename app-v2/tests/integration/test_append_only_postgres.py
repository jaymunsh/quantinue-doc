import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.core.ontology import ModelProvider
from quantinue.db.domain_records import CriticVerdictWrite, StrategistSignalWrite
from quantinue.db.postgres import PostgresRunStore

_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
_INT = TypeAdapter(int)


class _SourceRow(BaseModel):
    model_config = ConfigDict(strict=True)

    source: str
    source_ref: str
    captured_at: datetime
    confidence: Decimal
    summary: str | None
    evidence_id: str
    parent_evidence_ids: list[str]
    model_provider: str
    model_name: str | None
    prompt_version: str | None
    policy_version: str | None
    input_hash: str | None


class _StrategistRow(BaseModel):
    model_config = ConfigDict(strict=True)

    side: str
    conviction: Decimal
    summary: str
    evidence: list[str]


class _VerdictRow(BaseModel):
    model_config = ConfigDict(strict=True)

    ticker: str
    decision: str
    is_agreed: bool | None
    category: str
    objection: str
    confidence: Decimal
    decided_layer: str
    verdict_source: str


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_keeps_first_ledger_rows_when_conflicting_payload_replays() -> None:
    # Given
    assert _URL is not None
    cycle = datetime(2032, 1, 2, 13, 0, tzinfo=UTC)
    ticker = "LEDGERX"
    store = PostgresRunStore(_URL)
    await store.initialize()
    engine = create_async_engine(_URL)
    initial_signal = StrategistSignalWrite(
        run_id="initial-ledger-write",
        trade_date=cycle.date(),
        ticker=ticker,
        cycle_ts=cycle,
        side="buy",
        conviction=Decimal("0.8"),
        summary="first strategist summary",
        decision_close=Decimal(100),
        evidence=("initial:strategy",),
        inv_type="aggressive",
        disclosure_score=Decimal("0.8"),
        news_score=Decimal("0.7"),
    )
    initial_disclosure = DisclosureSourceRecord(
        filing_no="append-only-filing",
        title="first filing title",
        form_type="8-K",
        filed_at=cycle,
        event_type="other",
        source_ref="fixture://first-disclosure",
        summary="first disclosure summary",
        source="fixture",
        captured_at=cycle,
        confidence=0.9,
        evidence_id="initial:disclosure",
        model_provider=ModelProvider.MOCK,
        model_name="first-model",
        prompt_version="first-prompt",
        policy_version="first-policy",
        input_hash="1" * 64,
    )
    initial_news = NewsSourceRecord(
        news_key="append-only-news",
        title="first news title",
        url="fixture://first-news",
        source="fixture",
        published_at=cycle,
        summary="first news summary",
        captured_at=cycle,
        confidence=0.8,
        evidence_id="initial:news",
        parent_evidence_ids=("initial:disclosure",),
        model_provider=ModelProvider.MOCK,
        model_name="first-model",
        prompt_version="first-prompt",
        policy_version="first-policy",
        input_hash="2" * 64,
    )
    try:
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:trade_date,:ticker,'Ledger Contract',1)"""
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
        await store.domain.save_source_records(initial_signal, initial_disclosure, initial_news)
        signal_id = await store.domain.save_signal(initial_signal)
        verdict_id = await store.domain.save_verdict(
            CriticVerdictWrite(
                signal_id=signal_id,
                ticker=ticker,
                decision="pass",
                category="initial",
                objection="accepted",
                confidence=Decimal("0.8"),
                decided_layer="gate",
            )
        )
        async with engine.connect() as connection:
            source_rows = (
                (
                    await connection.execute(
                        text(
                            """SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_disclosure WHERE filing_no='append-only-filing'
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_news WHERE news_key='append-only-news' AND ticker='LEDGERX'
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_disclosure_signal WHERE ticker='LEDGERX' AND cycle_ts=:cycle
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_news_signal WHERE ticker='LEDGERX' AND cycle_ts=:cycle"""
                        ),
                        {"cycle": cycle},
                    )
                )
                .mappings()
                .all()
            )
            before_sources = tuple(_SourceRow.model_validate(dict(row)) for row in source_rows)
            before_signal = _StrategistRow.model_validate(
                dict(
                    (
                        await connection.execute(
                            text(
                                """SELECT side,conviction,summary,evidence
                                FROM tb_strategist_signals WHERE id=:id"""
                            ),
                            {"id": signal_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            )
            before_verdict = _VerdictRow.model_validate(
                dict(
                    (
                        await connection.execute(
                            text(
                                """SELECT ticker,decision,is_agreed,category,objection,confidence,
                                decided_layer,verdict_source
                                FROM tb_critic_verdict WHERE signal_id=:signal_id"""
                            ),
                            {"signal_id": signal_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            )

        conflicting_signal = StrategistSignalWrite(
            run_id="conflicting-replay",
            trade_date=cycle.date(),
            ticker=ticker,
            cycle_ts=cycle,
            side="hold",
            conviction=Decimal("0.001"),
            summary="conflicting strategist summary",
            decision_close=Decimal(1),
            evidence=("conflicting:strategy",),
            inv_type="aggressive",
            disclosure_score=Decimal("0.001"),
            news_score=Decimal("0.001"),
        )
        conflicting_disclosure = DisclosureSourceRecord(
            filing_no="append-only-filing",
            title="conflicting filing title",
            form_type="8-K",
            filed_at=cycle + timedelta(days=1),
            event_type="other",
            source_ref="adversarial://disclosure",
            summary="conflicting disclosure summary",
            source="adversarial-source",
            captured_at=cycle + timedelta(days=1),
            confidence=0.01,
            evidence_id="conflicting:disclosure",
            parent_evidence_ids=("conflicting:parent",),
            model_provider=ModelProvider.OPENAI,
            model_name="conflicting-model",
            prompt_version="conflicting-prompt",
            policy_version="conflicting-policy",
            input_hash="a" * 64,
        )
        conflicting_news = NewsSourceRecord(
            news_key="append-only-news",
            title="conflicting news title",
            url="adversarial://news",
            source="adversarial-source",
            published_at=cycle + timedelta(days=1),
            summary="conflicting news summary",
            captured_at=cycle + timedelta(days=1),
            confidence=0.01,
            evidence_id="conflicting:news",
            parent_evidence_ids=("conflicting:parent",),
            model_provider=ModelProvider.OPENAI,
            model_name="conflicting-model",
            prompt_version="conflicting-prompt",
            policy_version="conflicting-policy",
            input_hash="b" * 64,
        )

        # When
        for _ in range(2):
            await store.domain.save_source_records(
                conflicting_signal, conflicting_disclosure, conflicting_news
            )
            assert await store.domain.save_signal(conflicting_signal) == signal_id
            assert (
                await store.domain.save_verdict(
                    CriticVerdictWrite(
                        signal_id=signal_id,
                        ticker="ALTERED",
                        decision="reject",
                        category="conflicting",
                        objection="conflicting objection",
                        confidence=Decimal("0.001"),
                        decided_layer="hard_rule",
                        verdict_source="cache",
                    )
                )
            ) == verdict_id

        async with engine.connect() as connection:
            source_rows = (
                (
                    await connection.execute(
                        text(
                            """SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_disclosure WHERE filing_no='append-only-filing'
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_news WHERE news_key='append-only-news' AND ticker='LEDGERX'
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_disclosure_signal WHERE ticker='LEDGERX' AND cycle_ts=:cycle
                        UNION ALL
                        SELECT source,source_ref,captured_at,confidence,summary,evidence_id,
                        parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                        FROM tb_news_signal WHERE ticker='LEDGERX' AND cycle_ts=:cycle"""
                        ),
                        {"cycle": cycle},
                    )
                )
                .mappings()
                .all()
            )
            after_sources = tuple(_SourceRow.model_validate(dict(row)) for row in source_rows)
            after_signal = _StrategistRow.model_validate(
                dict(
                    (
                        await connection.execute(
                            text(
                                """SELECT side,conviction,summary,evidence
                                FROM tb_strategist_signals WHERE id=:id"""
                            ),
                            {"id": signal_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            )
            after_verdict = _VerdictRow.model_validate(
                dict(
                    (
                        await connection.execute(
                            text(
                                """SELECT ticker,decision,is_agreed,category,objection,confidence,
                                decided_layer,verdict_source
                                FROM tb_critic_verdict WHERE signal_id=:signal_id"""
                            ),
                            {"signal_id": signal_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            )

        # Then
        assert len(before_sources) == 4
        assert after_sources == before_sources
        assert after_signal == before_signal
        assert after_verdict == before_verdict
    finally:
        await engine.dispose()
        await store.close()
