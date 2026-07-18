"""Sequential 01 to 11 pipeline orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Final, Protocol

import anyio
import httpx2
import structlog
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from quantinue.core.context_detail import terminal_detail_from_context
from quantinue.core.contracts import PipelineContext, PipelineRequest, PipelineRun, RunStatus
from quantinue.core.errors import (
    AuthenticationFailureError,
    HardRiskFailureError,
    HttpFailureError,
    MissingStageDataError,
    RetryExhaustedError,
    TradingDisabledError,
    TransientFailureError,
    ValidationFailureError,
)
from quantinue.db.store import AttemptFailure
from quantinue.orchestration.domain_lifecycle import DomainLifecycle, NoopDomainLifecycle
from quantinue.orchestration.failure_policy import classify_failure
from quantinue.orchestration.lifecycle import deterministic_run_key
from quantinue.orchestration.policy import (
    DEFAULT_PIPELINE_POLICY,
    PipelinePolicy,
)
from quantinue.orchestration.retry import RetryPolicy, Sleeper

if TYPE_CHECKING:
    from collections.abc import Callable

    from quantinue.db.store import RunStore

ABANDON_TIMEOUT_SECONDS: Final = 5.0


class AnyioSleeper:
    """Production delay capability."""

    async def sleep(self, delay_seconds: float) -> None:
        """Yield for the configured backoff interval."""
        await anyio.sleep(delay_seconds)


class PipelineRole(Protocol):
    """Replaceable role contract shared by all component folders."""

    component: ClassVar[str]
    name: ClassVar[str]

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Consume upstream state and return a new immutable state."""
        ...


class AsyncCloseable(Protocol):
    """Application-lifetime resource owned by the orchestrator composition."""

    async def aclose(self) -> None:
        """Release the resource."""
        ...


class PipelineOrchestrator:
    """Run roles sequentially and persist one idempotent outcome."""

    def __init__(
        self,
        roles: tuple[PipelineRole, ...],
        store: RunStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        policy: PipelinePolicy = DEFAULT_PIPELINE_POLICY,
        sleeper: Sleeper | None = None,
    ) -> None:
        """Bind ordered roles to one run store."""
        self._roles: tuple[PipelineRole, ...] = roles
        self._store: RunStore = store
        self._clock = clock
        self._policy = policy
        self._sleeper = sleeper or AnyioSleeper()
        self._resources: tuple[AsyncCloseable, ...] = ()
        self._domain_lifecycle: DomainLifecycle = (
            store if isinstance(store, DomainLifecycle) else NoopDomainLifecycle()
        )
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("pipeline")

    async def close(self) -> None:
        """Close application-lifetime provider resources."""
        for resource in self._resources:
            await resource.aclose()

    def own_resource(self, resource: AsyncCloseable) -> None:
        """Register an application-lifetime provider during composition."""
        self._resources = (*self._resources, resource)

    async def run(self, request: PipelineRequest) -> PipelineRun:
        """Execute all roles or return a prior run for the same cycle."""
        key = str(deterministic_run_key(request.ticker, request.cycle_ts))
        claim = await self._store.claim(key, request, resume_failed=self._policy.resume_failed)
        while not claim.acquired:
            if claim.terminal_run is not None:
                return claim.terminal_run
            observed = await self._store.wait_for_release(key)
            if observed is not None:
                return observed
            claim = await self._store.claim(key, request, resume_failed=self._policy.resume_failed)
        context = claim.context
        if context is None:
            msg = "acquired run claim must include a checkpoint context"
            raise RuntimeError(msg)
        await self._logger.ainfo(
            "pipeline.run.started", run_id=context.run_id, ticker=request.ticker
        )
        released = False
        try:
            for role in self._roles[len(context.stages) :]:
                context = await self._execute_role(key, context, role)
            run = context.to_run()
            await self._store.finish_run(key, run)
            released = True
            await self._logger.ainfo(
                "pipeline.run.completed",
                run_id=run.run_id,
                ticker=run.ticker,
                stage_count=len(run.stages),
            )
            return run
        finally:
            if not released:
                with anyio.CancelScope(shield=True):
                    with anyio.fail_after(ABANDON_TIMEOUT_SECONDS):
                        await self._store.abandon(key)

    async def _execute_role(
        self, key: str, context: PipelineContext, role: PipelineRole
    ) -> PipelineContext:
        """Execute one role with a finite deadline and persisted attempt lifecycle."""
        retry = RetryPolicy(
            max_attempts=self._policy.role_max_retries + 1,
            initial_delay_seconds=self._policy.retry_base_delay_seconds,
        )
        for attempt_no in range(1, retry.max_attempts + 1):
            attempt = await self._store.start_attempt(key, role.component, self._clock())

            try:
                result = await _execute_before_deadline(
                    role, context, self._policy.role_timeout_seconds
                )
                result = await self._domain_lifecycle.stage_completed(
                    role.component, context, result
                )
                await self._store.complete_stage(key, result, attempt)
            except (
                AuthenticationFailureError,
                HardRiskFailureError,
                HttpFailureError,
                MissingStageDataError,
                RetryExhaustedError,
                TradingDisabledError,
                TransientFailureError,
                ValidationFailureError,
                ValidationError,
                httpx2.TransportError,
                ConnectionError,
                OSError,
                RuntimeError,
                TypeError,
                AttributeError,
                LookupError,
                ArithmeticError,
                AssertionError,
                TimeoutError,
                SQLAlchemyError,
            ) as error:
                decision = classify_failure(error)
                has_budget = attempt_no < retry.max_attempts
                if decision.retryable and has_budget:
                    retrying = AttemptFailure(
                        "retrying", decision.failure.error_code, decision.failure.error_message
                    )
                    await self._store.fail_attempt(key, attempt, self._clock(), retrying)
                    await self._sleeper.sleep(retry.delay_after(attempt_no))
                    continue
                await self._store.fail_attempt(key, attempt, self._clock(), decision.failure)
                await self._record_failure(key, context, resumable=decision.retryable)
                raise
            return result
        msg = "positive attempt budget must return or raise"
        raise RuntimeError(msg)

    async def _record_failure(
        self,
        key: str,
        context: PipelineContext,
        *,
        resumable: bool,
    ) -> None:
        """Publish the failed attempt and partial run before propagating."""
        failed = PipelineRun(
            run_id=context.run_id,
            ticker=context.request.ticker,
            cycle_ts=context.request.cycle_ts,
            status=RunStatus.FAILED,
            stages=context.stages,
            evidence_trace=context.evidence_trace,
            conviction=context.conviction,
            side=context.side,
            account_id=context.account_id,
            detail=terminal_detail_from_context(context),
            order=context.order,
            review=context.review,
        )
        await self._store.finish_run(key, failed, resumable=resumable)


async def _execute_before_deadline(
    role: PipelineRole, context: PipelineContext, timeout_seconds: float
) -> PipelineContext:
    """Execute a role under a native cancellation deadline."""
    result = context
    with anyio.move_on_after(timeout_seconds) as deadline:
        result = await role.execute(context)
    if deadline.cancel_called:
        raise TimeoutError
    return result
