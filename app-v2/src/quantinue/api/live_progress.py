"""Safe current and next-stage projections for control-room runs."""

from typing import Final, assert_never

from quantinue.api.schemas import LiveStageView
from quantinue.core.contracts import PipelineRun, RunStatus, StageStatus
from quantinue.core.ontology import StageAttemptState
from quantinue.db.contracts import PersistedAttempt

CANONICAL_STAGES: Final[tuple[tuple[str, str], ...]] = (
    ("01", "1차 스크리너"),
    ("02", "기술 분석"),
    ("03", "2차 스크리너"),
    ("04", "매크로 분석"),
    ("05", "공시 분석"),
    ("06", "뉴스 분석"),
    ("07", "전략 종합"),
    ("08", "크리틱 검증"),
    ("09", "리스크·포트폴리오"),
    ("10", "주문·체결"),
    ("11", "리뷰·회고"),
)
STAGE_NAME_BY_COMPONENT: Final[dict[str, str]] = dict(CANONICAL_STAGES)
ATTEMPT_STATE_BY_VALUE: Final[dict[str, StageAttemptState]] = {
    state.value: state for state in StageAttemptState
}


def ui_stage_status(raw: str) -> StageStatus:
    """Map persisted retry-aware attempt states to safe UI stage states."""
    state = ATTEMPT_STATE_BY_VALUE.get(raw)
    if state is None:
        return StageStatus.FAILED
    match state:
        case StageAttemptState.PENDING:
            return StageStatus.PENDING
        case StageAttemptState.RUNNING:
            return StageStatus.RUNNING
        case StageAttemptState.RETRYING:
            return StageStatus.RETRYING
        case StageAttemptState.COMPLETED:
            return StageStatus.COMPLETED
        case StageAttemptState.FAILED | StageAttemptState.TIMED_OUT:
            return StageStatus.FAILED
        case unreachable:
            assert_never(unreachable)


def live_stage_views(
    run: PipelineRun, attempts: tuple[PersistedAttempt, ...]
) -> tuple[LiveStageView | None, LiveStageView | None]:
    """Derive the safe current attempt and next canonical stage for active runs."""
    if run.status not in {RunStatus.RUNNING, RunStatus.RETRYING}:
        return None, None
    latest_by_component = {attempt.component: attempt for attempt in attempts}
    latest_active = next(
        (
            attempt
            for attempt in reversed(attempts)
            if attempt.component in STAGE_NAME_BY_COMPONENT
            and attempt.status in {"running", "retrying"}
        ),
        None,
    )
    completed = {stage.component for stage in run.stages if stage.status is StageStatus.COMPLETED}
    current_component = (
        latest_active.component
        if latest_active is not None
        else next(
            (component for component, _ in CANONICAL_STAGES if component not in completed), None
        )
    )
    if current_component is None:
        return None, None
    active_attempt = latest_by_component.get(current_component)
    current = LiveStageView(
        component=current_component,
        name=STAGE_NAME_BY_COMPONENT.get(current_component, f"Stage {current_component}"),
        status=(
            ui_stage_status(active_attempt.status)
            if active_attempt is not None
            else StageStatus.PENDING
        ),
    )
    current_index = next(
        index
        for index, (component, _) in enumerate(CANONICAL_STAGES)
        if component == current_component
    )
    next_stage = None
    if current_index + 1 < len(CANONICAL_STAGES):
        component, name = CANONICAL_STAGES[current_index + 1]
        next_stage = LiveStageView(component=component, name=name, status=StageStatus.PENDING)
    return current, next_stage
