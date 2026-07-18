"""Safe, read-only snapshots for in-progress pipeline runs."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from quantinue.core.context_detail import terminal_detail_from_context
from quantinue.core.contracts import (
    PipelineContext,
    PipelineRun,
    RoleEvidenceTrace,
    RunId,
    RunStatus,
    StageResult,
)
from quantinue.core.terminal_detail import TerminalRunDetail
from quantinue.core.terminal_run_types import OrderResult, ReviewResult


class _AttemptRecord(Protocol):
    """Minimum durable attempt fields required by an active projection."""

    @property
    def component(self) -> str: ...

    @property
    def attempt_no(self) -> int: ...

    @property
    def status(self) -> str: ...

    @property
    def started_at(self) -> datetime: ...

    @property
    def finished_at(self) -> datetime | None: ...

    @property
    def error_code(self) -> str | None: ...


class ActiveAttemptSnapshot(BaseModel):
    """One progress attempt without its persisted raw error message."""

    model_config = ConfigDict(frozen=True)

    component: str
    attempt_no: int = Field(gt=0)
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None


class ActivePipelineSnapshot(BaseModel):
    """Safe checkpoint projection plus the observable in-progress attempts."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    ticker: str
    cycle_ts: datetime
    status: RunStatus
    stages: tuple[StageResult, ...]
    evidence_trace: tuple[RoleEvidenceTrace, ...]
    conviction: float | None
    side: str | None
    detail: TerminalRunDetail
    order: OrderResult | None
    review: ReviewResult | None
    attempts: tuple[ActiveAttemptSnapshot, ...]

    def to_run(self) -> PipelineRun:
        """Build the compatible run projection consumed by control-room presentation."""
        return PipelineRun(
            run_id=self.run_id,
            ticker=self.ticker,
            cycle_ts=self.cycle_ts,
            status=self.status,
            stages=self.stages,
            evidence_trace=self.evidence_trace,
            conviction=self.conviction,
            side=self.side,
            detail=self.detail,
            order=self.order,
            review=self.review,
        )


def active_pipeline_snapshot(
    context: PipelineContext, attempts: tuple[_AttemptRecord, ...]
) -> ActivePipelineSnapshot:
    """Project durable context and attempts without provider exception text."""
    return ActivePipelineSnapshot(
        run_id=context.run_id,
        ticker=context.request.ticker,
        cycle_ts=context.request.cycle_ts,
        status=_active_status(attempts),
        stages=context.stages,
        evidence_trace=context.evidence_trace,
        conviction=context.conviction,
        side=context.side,
        detail=terminal_detail_from_context(context),
        order=context.order,
        review=context.review,
        attempts=tuple(
            ActiveAttemptSnapshot(
                component=attempt.component,
                attempt_no=attempt.attempt_no,
                status=attempt.status,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                error_code=attempt.error_code,
            )
            for attempt in attempts
        ),
    )


def _active_status(attempts: tuple[_AttemptRecord, ...]) -> RunStatus:
    """Derive retry wait state from the latest durable attempt."""
    if attempts and attempts[-1].status == RunStatus.RETRYING.value:
        return RunStatus.RETRYING
    return RunStatus.RUNNING
