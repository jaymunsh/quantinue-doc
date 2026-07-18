"""Regression coverage for PostgreSQL checkpoint-backed resumption."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy.exc import OperationalError

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.db.store import PostgresRunStore
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.orchestration.policy import load_pipeline_policy

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
PERSISTENCE_STAGE = "stage"
DATABASE_UNAVAILABLE = "database unavailable"


class _CheckpointRole:
    """Persist context used by the later resumption attempt."""

    component: ClassVar[str] = "01"
    name: ClassVar[str] = "checkpoint"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        return replace(context, last_price=100.0).add_stage(self.component, self.name, "saved")


class _ResumeAfterCheckpointRole:
    """Fail once after stage 01 so resume must read the checkpoint payload."""

    component: ClassVar[str] = "02"
    name: ClassVar[str] = "resume-after-checkpoint"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError
        assert context.last_price == 100.0
        return context.add_stage(self.component, self.name, "resumed from checkpoint")


class _PersistenceUnavailableAfterCheckpointRole:
    component: ClassVar[str] = "02"
    name: ClassVar[str] = "persistence-unavailable"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls == 1:
            raise OperationalError(PERSISTENCE_STAGE, {}, RuntimeError(DATABASE_UNAVAILABLE))
        assert context.last_price == 100.0
        return context.add_stage(self.component, self.name, "resumed after database outage")


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_failed_run_resumes_from_checkpoint_not_terminal_payload() -> None:
    """Given terminal PipelineRun payload, resume restores its latest PipelineContext."""
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    first = _CheckpointRole()
    second = _ResumeAfterCheckpointRole()
    policy = load_pipeline_policy(Path("config/pipeline.yaml")).model_copy(
        update={"role_max_retries": 0}
    )
    request = PipelineRequest(ticker="PGCHECK", cycle_ts=datetime.now(UTC))

    with pytest.raises(TimeoutError):
        _ = await PipelineOrchestrator((first, second), store, policy=policy).run(request)

    failed = next(run for run in await store.list_recent(100) if run.ticker == "PGCHECK")
    assert [stage.component for stage in failed.stages] == ["01"]

    resumed = await PipelineOrchestrator((first, second), store, policy=policy).run(request)

    assert resumed.run_id == failed.run_id
    assert [stage.component for stage in resumed.stages] == ["01", "02"]
    assert first.calls == 1
    assert [
        (item.component, item.attempt_no, item.status)
        for item in await store.list_attempts(resumed.run_id)
    ] == [
        ("01", 1, "completed"),
        ("02", 1, "timed_out"),
        ("02", 2, "completed"),
    ]
    await store.close()


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_persistence_unavailable_run_resumes_from_checkpoint() -> None:
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    first = _CheckpointRole()
    second = _PersistenceUnavailableAfterCheckpointRole()
    policy = load_pipeline_policy(Path("config/pipeline.yaml")).model_copy(
        update={"role_max_retries": 0}
    )
    request = PipelineRequest(ticker="PGPERSIST", cycle_ts=datetime.now(UTC))

    try:
        with pytest.raises(OperationalError):
            _ = await PipelineOrchestrator((first, second), store, policy=policy).run(request)

        resumed = await PipelineOrchestrator((first, second), store, policy=policy).run(request)

        assert [stage.component for stage in resumed.stages] == ["01", "02"]
        assert first.calls == 1
        assert second.calls == 2
    finally:
        await store.close()
