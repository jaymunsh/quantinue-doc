from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

import anyio
import httpx2
import pytest
from sqlalchemy.exc import IntegrityError
from typing_extensions import override

from quantinue.core.contracts import PipelineContext, PipelineRequest, RunStatus
from quantinue.core.errors import (
    AuthenticationFailureError,
    HttpFailureError,
    TransientFailureError,
    ValidationFailureError,
)
from quantinue.db.store import InMemoryRunStore
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.orchestration.policy import PipelinePolicy, ThresholdPolicy
from quantinue.orchestration.retry import Sleeper

NOW = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)
FIXTURE_PROVIDER = "fixture"
TRANSIENT_REASON = "unavailable"
ONE_OUTAGE_REASON = "one outage"
INVALID_FIELD = "input"
INVALID_REASON = "invalid"
RAW_PROVIDER_DETAIL = "private-provider-detail"
RAW_WIRE_DETAIL = "private wire detail"
STAGE_PROJECTION = "stage projection"
PRIVATE_DATABASE_DETAIL = "private database detail"


class NoSleep(Sleeper):
    @override
    async def sleep(self, delay_seconds: float) -> None:
        del delay_seconds


def policy(*, attempts: int = 3, timeout: float = 1.0) -> PipelinePolicy:
    return PipelinePolicy(
        role_timeout_seconds=timeout,
        role_max_retries=attempts - 1,
        retry_base_delay_seconds=0,
        resume_failed=True,
        thresholds=ThresholdPolicy(
            minimum_confidence=0.6,
            strategist_buy_score=0.65,
            critic_approval_score=0.6,
            maximum_risk_score=0.7,
        ),
    )


class TransientTwice:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "transient"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls < 3:
            raise TransientFailureError(FIXTURE_PROVIDER, TRANSIENT_REASON)
        return context.add_stage(self.component, self.name, "ok")


class Hangs:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "hang"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        await anyio.sleep_forever()
        return context


class ValidationFails:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "invalid"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        del context
        self.calls += 1
        raise ValidationFailureError(INVALID_FIELD, INVALID_REASON)


class First:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "first"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        return replace(context, last_price=1).add_stage("01", self.name, "first")


class FailsOnce:
    component: ClassVar[str] = "02"
    name: ClassVar[str] = "second"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls == 1:
            raise TransientFailureError(FIXTURE_PROVIDER, ONE_OUTAGE_REASON)
        return context.add_stage("02", self.name, "second")


class RawFailure:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "raw"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        del context
        raise RuntimeError(RAW_PROVIDER_DETAIL)


class TransportFailsOnce:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "transport"

    def __init__(self, error_type: type[httpx2.TransportError]) -> None:
        self.calls = 0
        self._error_type = error_type

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self.calls += 1
        if self.calls == 1:
            request = httpx2.Request("GET", "https://provider.invalid")
            raise self._error_type(RAW_WIRE_DETAIL, request=request)
        return context.add_stage(self.component, self.name, "recovered")


class TerminalProviderFailure:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "terminal-provider"

    def __init__(self, error: AuthenticationFailureError | HttpFailureError) -> None:
        self.calls = 0
        self._error = error

    async def execute(self, context: PipelineContext) -> PipelineContext:
        del context
        self.calls += 1
        raise self._error


class IntegrityFailingStore(InMemoryRunStore):
    async def stage_completed(
        self, component: str, previous: PipelineContext, result: PipelineContext
    ) -> PipelineContext:
        del component, previous, result
        raise IntegrityError(STAGE_PROJECTION, {}, RuntimeError(PRIVATE_DATABASE_DETAIL))


@pytest.mark.anyio
async def test_transient_attempts_persist_retrying_then_completed() -> None:
    store = InMemoryRunStore()
    role = TransientTwice()
    run = await PipelineOrchestrator((role,), store, policy=policy(), sleeper=NoSleep()).run(
        PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    )
    attempts = await store.list_attempts(run.run_id)
    assert [item.status for item in attempts] == ["retrying", "retrying", "completed"]


@pytest.mark.anyio
async def test_timeout_attempts_are_persisted_without_raw_message() -> None:
    store = InMemoryRunStore()
    with pytest.raises(TimeoutError):
        _ = await PipelineOrchestrator(
            (Hangs(),), store, policy=policy(attempts=1, timeout=0.001)
        ).run(PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    failed = (await store.list_recent())[0]
    attempts = await store.list_attempts(failed.run_id)
    assert attempts[0].status == "timed_out"
    assert attempts[0].error_code == "ROLE_TIMEOUT"
    assert attempts[0].error_message == "role execution timed out"


@pytest.mark.anyio
async def test_validation_failure_is_terminal_after_one_attempt() -> None:
    store = InMemoryRunStore()
    role = ValidationFails()
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    with pytest.raises(ValidationFailureError):
        _ = await PipelineOrchestrator((role,), store, policy=policy()).run(request)
    repeated = await PipelineOrchestrator((role,), store, policy=policy()).run(request)
    assert repeated.status is RunStatus.FAILED
    assert role.calls == 1


@pytest.mark.anyio
async def test_failed_transient_run_resumes_from_completed_checkpoint() -> None:
    store = InMemoryRunStore()
    second = FailsOnce()
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    with pytest.raises(TransientFailureError):
        _ = await PipelineOrchestrator(
            (First(), second), store, policy=policy(attempts=1), sleeper=NoSleep()
        ).run(request)
    failed = (await store.list_recent())[0]
    assert [trace.component for trace in failed.evidence_trace] == ["01"]
    resumed = await PipelineOrchestrator(
        (First(), second), store, policy=policy(attempts=1), sleeper=NoSleep()
    ).run(request)
    assert resumed.status is RunStatus.COMPLETED
    assert [stage.component for stage in resumed.stages] == ["01", "02"]


@pytest.mark.anyio
async def test_unknown_failure_is_redacted_in_persistence_and_reraised() -> None:
    store = InMemoryRunStore()
    with pytest.raises(RuntimeError, match="private-provider"):
        _ = await PipelineOrchestrator((RawFailure(),), store, policy=policy()).run(
            PipelineRequest(ticker="NVDA", cycle_ts=NOW)
        )
    failed = (await store.list_recent())[0]
    attempt = (await store.list_attempts(failed.run_id))[0]
    assert attempt.error_code == "UNEXPECTED_ROLE_FAILURE"
    assert attempt.error_message == "unexpected role failure"
    assert "private-provider" not in attempt.error_message


@pytest.mark.parametrize(
    "error_type", [httpx2.ConnectError, httpx2.ReadError, httpx2.RemoteProtocolError]
)
@pytest.mark.anyio
async def test_transport_failure_retries_in_shipping_pipeline(
    error_type: type[httpx2.TransportError],
) -> None:
    store = InMemoryRunStore()
    role = TransportFailsOnce(error_type)
    run = await PipelineOrchestrator((role,), store, policy=policy(), sleeper=NoSleep()).run(
        PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    )
    attempts = await store.list_attempts(run.run_id)
    assert [attempt.status for attempt in attempts] == ["retrying", "completed"]
    assert attempts[0].error_code == "TRANSPORT_FAILURE"
    assert attempts[0].error_message == "provider transport failed"


@pytest.mark.anyio
async def test_exhausted_transport_failure_resumes_under_explicit_policy() -> None:
    store = InMemoryRunStore()
    role = TransportFailsOnce(httpx2.ConnectError)
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    with pytest.raises(httpx2.ConnectError):
        _ = await PipelineOrchestrator(
            (role,), store, policy=policy(attempts=1), sleeper=NoSleep()
        ).run(request)
    failed = (await store.list_recent())[0]
    assert (await store.list_attempts(failed.run_id))[0].error_code == "TRANSPORT_FAILURE"

    resumed = await PipelineOrchestrator(
        (role,), store, policy=policy(attempts=1), sleeper=NoSleep()
    ).run(request)
    assert resumed.status is RunStatus.COMPLETED
    assert [attempt.status for attempt in await store.list_attempts(resumed.run_id)] == [
        "failed",
        "completed",
    ]


@pytest.mark.parametrize(
    "error",
    [AuthenticationFailureError("provider"), HttpFailureError(400), HttpFailureError(401)],
)
@pytest.mark.anyio
async def test_auth_and_nontransient_http_status_are_not_retried(
    error: AuthenticationFailureError | HttpFailureError,
) -> None:
    store = InMemoryRunStore()
    role = TerminalProviderFailure(error)
    with pytest.raises(type(error)):
        _ = await PipelineOrchestrator((role,), store, policy=policy(), sleeper=NoSleep()).run(
            PipelineRequest(ticker="NVDA", cycle_ts=NOW)
        )
    assert role.calls == 1


@pytest.mark.anyio
async def test_post_role_persistence_failure_finalizes_attempt_and_run() -> None:
    store = IntegrityFailingStore()
    role = TransientTwice()
    role.calls = 2
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    with pytest.raises(IntegrityError):
        _ = await PipelineOrchestrator((role,), store, policy=policy()).run(request)

    failed = (await store.list_recent())[0]
    attempts = await store.list_attempts(failed.run_id)
    assert failed.status is RunStatus.FAILED
    assert [attempt.status for attempt in attempts] == ["failed"]
    assert attempts[0].error_code == "PERSISTENCE_CONFLICT"
    assert attempts[0].error_message == "persistence constraint rejected stage"
