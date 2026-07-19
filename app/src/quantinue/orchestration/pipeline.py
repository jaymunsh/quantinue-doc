"""Sequential 01 to 11 pipeline orchestrator."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Final, Protocol

import anyio
import structlog

from quantinue.core.context_detail import terminal_detail_from_context
from quantinue.core.contracts import PipelineContext, PipelineRequest, PipelineRun, RunId, RunStatus
from quantinue.core.errors import (
    MissingStageDataError,
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
from quantinue.roles.role_02_technical_analysis.service import technical_score

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


def _rebind_candidate_seed(
    seed: PipelineContext,
    request: PipelineRequest,
    run_id: RunId,
) -> PipelineContext:
    source_run_id = str(seed.run_id)
    target_run_id = str(run_id)

    def evidence_ids(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(value.replace(source_run_id, target_run_id, 1) for value in values)

    evidence_trace = tuple(
        item.model_copy(
            update={
                "run_id": run_id,
                "evidence_id": item.evidence_id.replace(source_run_id, target_run_id, 1),
                "parent_evidence_ids": evidence_ids(item.parent_evidence_ids),
            }
        )
        for item in seed.evidence_trace
    )
    universe_output = seed.universe_output
    if universe_output is not None:
        universe_output = universe_output.model_copy(
            update={
                "run_id": target_run_id,
                "members": tuple(
                    item.model_copy(update={"evidence_ids": evidence_ids(item.evidence_ids)})
                    for item in universe_output.members
                ),
            }
        )
    technical_output = seed.technical_output
    if technical_output is not None:
        technical_output = technical_output.model_copy(
            update={
                "run_id": target_run_id,
                "snapshots": tuple(
                    item.model_copy(update={"evidence_ids": evidence_ids(item.evidence_ids)})
                    for item in technical_output.snapshots
                ),
            }
        )
    daily_output = seed.daily_screener_output
    if daily_output is not None:
        daily_output = daily_output.model_copy(
            update={
                "run_id": target_run_id,
                "picks": tuple(
                    item.model_copy(update={"evidence_ids": evidence_ids(item.evidence_ids)})
                    for item in daily_output.picks
                ),
            }
        )
    macro_output = seed.macro_output
    if macro_output is not None:
        macro_output = macro_output.model_copy(
            update={
                "run_id": target_run_id,
                "evidence_ids": evidence_ids(macro_output.evidence_ids),
            }
        )
    return replace(
        seed,
        run_id=run_id,
        request=request,
        evidence_trace=evidence_trace,
        universe_output=universe_output,
        technical_output=technical_output,
        daily_screener_output=daily_output,
        macro_output=macro_output,
    )


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
        return await self._run_claimed(request)

    async def run_screening(self, request: PipelineRequest) -> tuple[PipelineRun, ...]:
        """Discover once, then execute the candidate-specific roles in rank order."""
        request = request.model_copy(update={"automatic": True})
        discovery = PipelineContext(request=request)
        for role in self._roles[:4]:
            previous = discovery
            discovery = await _execute_before_deadline(
                role, discovery, self._policy.role_timeout_seconds
            )
            discovery = await self._domain_lifecycle.stage_completed(
                role.component, previous, discovery
            )
        daily = discovery.daily_screener_output
        technical = discovery.technical_output
        if daily is None or technical is None:
            component = "03"
            field_name = "daily_screener_output"
            raise MissingStageDataError(component, field_name)
        snapshots = {item.ticker: item for item in technical.snapshots}
        completed: list[PipelineRun] = []
        for pick in daily.picks:
            snapshot = snapshots.get(pick.ticker)
            if snapshot is None:
                component = "03"
                field_name = f"technical_snapshot:{pick.ticker}"
                raise MissingStageDataError(component, field_name)
            candidate_request = PipelineRequest(
                ticker=pick.ticker,
                cycle_ts=request.cycle_ts,
                automatic=True,
            )
            seed = replace(
                discovery,
                request=candidate_request,
                last_price=snapshot.close,
                technical_score=technical_score(snapshot),
                is_daily_pick=True,
                candidate_rank=pick.rank,
            )
            try:
                completed.append(await self._run_claimed(candidate_request, seed=seed))
            except Exception:  # noqa: BLE001 - candidate boundary isolates failed siblings.
                await self._logger.aexception("pipeline.candidate.failed", ticker=pick.ticker)
        return tuple(completed)

    async def _run_claimed(
        self,
        request: PipelineRequest,
        *,
        seed: PipelineContext | None = None,
    ) -> PipelineRun:
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
        released = False
        try:
            if seed is not None and not context.stages:
                context = _rebind_candidate_seed(seed, request, context.run_id)
                for component in ("01", "03", "04"):
                    context = await self._domain_lifecycle.stage_completed(
                        component, context, context
                    )
                await self._store.seed_context(key, context)
            await self._logger.ainfo(
                "pipeline.run.started", run_id=context.run_id, ticker=request.ticker
            )
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
            except Exception as error:
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
            automatic=context.request.automatic,
            candidate_rank=context.candidate_rank,
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
