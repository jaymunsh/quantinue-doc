"""Real PostgreSQL regression coverage for source and review evidence lineage."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import PipelineRequest
from quantinue.core.ontology import ModelProvider
from quantinue.db.postgres import PostgresRunStore
from quantinue.db.reviews import PostgresReviewRepository
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.roles.role_11_reviewer.processor import ReviewSnapshotWrite

_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
_INT = TypeAdapter(int)


class _ProvenanceRow(BaseModel):
    """Typed projection of the auditable columns selected from PostgreSQL."""

    model_config = ConfigDict(strict=True)

    source_ref: str
    captured_at: datetime
    confidence: Decimal
    evidence_id: str
    parent_evidence_ids: list[str]
    model_provider: str | None
    model_name: str | None
    prompt_version: str | None
    policy_version: str | None
    input_hash: str | None


class _ReviewSnapshotRow(_ProvenanceRow):
    """T+5 projection additionally includes the official close."""

    close: Decimal


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_preserves_model_and_source_lineage_in_normalized_records() -> None:
    """Source ledgers and T+5 snapshots retain auditable rather than lossy evidence."""
    # Given
    assert _URL is not None
    cycle = datetime(2026, 7, 12, 13, 0, tzinfo=UTC)
    store = PostgresRunStore(_URL)
    await store.initialize()
    orchestrator = PipelineOrchestrator(
        build_roles(DeterministicAnalyzer(), MockBroker(), store=store), store
    )

    # When
    run = await orchestrator.run(PipelineRequest(ticker="NVDA", cycle_ts=cycle))
    engine = create_async_engine(_URL)
    async with engine.connect() as connection:
        disclosure_snapshot = _ProvenanceRow.model_validate(
            dict(
                (
                    await connection.execute(
                        text(
                            """SELECT source_ref,captured_at,confidence,evidence_id,
                    parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                    FROM tb_disclosure_signal WHERE ticker='NVDA' AND cycle_ts=:cycle"""
                        ),
                        {"cycle": cycle},
                    )
                )
                .mappings()
                .one()
            )
        )
        news_snapshot = _ProvenanceRow.model_validate(
            dict(
                (
                    await connection.execute(
                        text(
                            """SELECT source_ref,captured_at,confidence,evidence_id,
                    parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                    FROM tb_news_signal WHERE ticker='NVDA' AND cycle_ts=:cycle"""
                        ),
                        {"cycle": cycle},
                    )
                )
                .mappings()
                .one()
            )
        )
        signal_id = _INT.validate_python(
            await connection.scalar(
                text(
                    "SELECT id FROM tb_strategist_signals WHERE ticker='NVDA' AND cycle_ts=:cycle"
                ),
                {"cycle": cycle},
            )
        )
    reviews = PostgresReviewRepository(_URL)
    await reviews.initialize()
    snapshot = ReviewSnapshotWrite(
        signal_id=signal_id,
        day_offset=1,
        price_date=cycle.date(),
        close=Decimal("129.50"),
        source="market_data",
        source_ref="fixture://close/NVDA/2026-07-12",
        observed_at=cycle,
        captured_at=cycle,
        confidence=0.97,
        evidence_id=f"{run.run_id}:11:close:1",
        parent_evidence_ids=(f"{run.run_id}:07:strategy",),
        model_provider=ModelProvider.LOCAL,
        model_name="review-local-v1",
        prompt_version="role-11-v1",
        policy_version="review-policy-v1",
        input_hash="a" * 64,
    )
    await reviews.save_snapshot(snapshot)
    newer_snapshot = replace(
        snapshot,
        close=Decimal("131.25"),
        source_ref="fixture://close/NVDA/2026-07-12/corrected",
        captured_at=cycle + timedelta(minutes=10),
        confidence=0.97,
        evidence_id=f"{run.run_id}:11:close:1:corrected",
        parent_evidence_ids=(f"{run.run_id}:08:critic",),
        model_provider=ModelProvider.OPENAI,
        model_name="review-openai-v1",
        prompt_version="role-11-v2",
        policy_version="review-policy-v2",
        input_hash="b" * 64,
    )
    await reviews.save_snapshot(newer_snapshot)
    stale_snapshot = replace(
        snapshot,
        close=Decimal("0.10"),
        source_ref="fixture://stale-overwrite",
        confidence=0.10,
        evidence_id=f"{run.run_id}:11:close:1:stale",
        parent_evidence_ids=(f"{run.run_id}:stale",),
        model_provider=ModelProvider.LOCAL,
        model_name="stale-local",
        prompt_version="stale-prompt",
        policy_version="stale-policy",
        input_hash="c" * 64,
    )
    await reviews.save_snapshot(stale_snapshot)
    await reviews.close()
    async with engine.connect() as connection:
        persisted_snapshot = _ReviewSnapshotRow.model_validate(
            dict(
                (
                    await connection.execute(
                        text(
                            """SELECT close,source_ref,captured_at,confidence,evidence_id,
                    parent_evidence_ids,model_provider,model_name,prompt_version,policy_version,input_hash
                    FROM tb_review_price_snapshots WHERE signal_id=:signal_id AND day_offset=1"""
                        ),
                        {"signal_id": signal_id},
                    )
                )
                .mappings()
                .one()
            )
        )
    await engine.dispose()
    await store.close()

    # Then
    for row, expected_ref, expected_evidence, expected_parents in (
        (
            disclosure_snapshot,
            "sec://filing/fixture-filing",
            f"{run.run_id}:05:disclosure",
            [],
        ),
        (
            news_snapshot,
            "https://example.invalid/fixture-news",
            f"{run.run_id}:06:news",
            [f"{run.run_id}:05:disclosure"],
        ),
    ):
        assert row.source_ref == expected_ref
        assert row.captured_at == cycle
        assert row.confidence > 0
        assert row.evidence_id == expected_evidence
        assert row.parent_evidence_ids == expected_parents
        assert row.model_provider == "mock"
        assert row.model_name == "deterministic-mock-v1"
        assert row.prompt_version
        assert row.policy_version
        assert row.input_hash is not None
        assert len(row.input_hash) == 64
    assert persisted_snapshot.source_ref == newer_snapshot.source_ref
    assert persisted_snapshot.captured_at == newer_snapshot.captured_at
    assert persisted_snapshot.close == newer_snapshot.close
    assert persisted_snapshot.confidence == Decimal("0.97")
    assert persisted_snapshot.evidence_id == newer_snapshot.evidence_id
    assert persisted_snapshot.parent_evidence_ids == list(newer_snapshot.parent_evidence_ids)
    assert persisted_snapshot.model_provider == "openai"
    assert persisted_snapshot.model_name == "review-openai-v1"
    assert persisted_snapshot.prompt_version == "role-11-v2"
    assert persisted_snapshot.policy_version == "review-policy-v2"
    assert persisted_snapshot.input_hash == "b" * 64
