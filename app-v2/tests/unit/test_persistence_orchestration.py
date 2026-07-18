from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

import anyio
import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest, PipelineRun, RunStatus
from quantinue.db.store import InMemoryRunStore
from quantinue.orchestration.pipeline import PipelineOrchestrator

NOW = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)


class CountingRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "counter"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        await anyio.sleep(0.001)
        return context.add_stage(self.component, self.name, "counted")


class FirstRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "first"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        return replace(context, last_price=100.0).add_stage(self.component, self.name, "done")


class InterruptOnceRole:
    component: ClassVar[str] = "02"
    name: ClassVar[str] = "interrupt-once"

    def __init__(self) -> None:
        self.interrupted = False

    async def execute(self, context: PipelineContext) -> PipelineContext:
        if not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt
        assert context.last_price == 100.0
        return context.add_stage(self.component, self.name, "resumed")


class FailingRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "failure"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        del context
        msg = "fixture failure"
        raise RuntimeError(msg)


class BlockingRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "blocking"

    def __init__(self, entered: anyio.Event) -> None:
        self._entered = entered

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self._entered.set()
        await anyio.sleep_forever()
        return context


@pytest.mark.anyio
async def test_two_concurrent_identical_requests_execute_roles_once() -> None:
    store = InMemoryRunStore()
    role = CountingRole()
    orchestrator = PipelineOrchestrator((role,), store)
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    results: list[PipelineRun] = []

    async def run_once() -> None:
        results.append(await orchestrator.run(request))

    async with anyio.create_task_group() as group:
        _ = group.start_soon(run_once)
        _ = group.start_soon(run_once)

    assert role.calls == 1
    assert len(results) == 2
    assert results[0].run_id == results[1].run_id


@pytest.mark.anyio
async def test_interrupted_run_resumes_without_repeating_completed_checkpoint() -> None:
    store = InMemoryRunStore()
    first = FirstRole()
    interrupted = InterruptOnceRole()
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    with pytest.raises(KeyboardInterrupt):
        _ = await PipelineOrchestrator((first, interrupted), store).run(request)

    resumed = await PipelineOrchestrator((first, interrupted), store).run(request)

    assert resumed.status is RunStatus.COMPLETED
    assert [stage.component for stage in resumed.stages] == ["01", "02"]
    assert first.calls == 1
    attempts = await store.list_attempts(resumed.run_id)
    assert [(attempt.component, attempt.attempt_no, attempt.status) for attempt in attempts] == [
        ("01", 1, "completed"),
        ("02", 1, "failed"),
        ("02", 2, "completed"),
    ]


@pytest.mark.anyio
async def test_failed_run_and_attempt_remain_observable() -> None:
    store = InMemoryRunStore()
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    with pytest.raises(RuntimeError, match="fixture failure"):
        _ = await PipelineOrchestrator((FailingRole(),), store).run(request)

    failed = (await store.list_recent())[0]
    assert failed.status is RunStatus.FAILED
    attempts = await store.list_attempts(failed.run_id)
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].error_code == "UNEXPECTED_ROLE_FAILURE"
    assert attempts[0].error_message == "unexpected role failure"


@pytest.mark.anyio
async def test_cancelled_owner_releases_claim_for_immediate_resume() -> None:
    store = InMemoryRunStore()
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    entered = anyio.Event()

    async with anyio.create_task_group() as group:
        _ = group.start_soon(PipelineOrchestrator((BlockingRole(entered),), store).run, request)
        await entered.wait()
        group.cancel_scope.cancel()

    with anyio.fail_after(0.2):
        resumed = await PipelineOrchestrator((CountingRole(),), store).run(request)
    assert resumed.status is RunStatus.COMPLETED
