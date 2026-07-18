from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import RunStatus, StageStatus
from quantinue.core.errors import InvalidTransitionError
from quantinue.orchestration.lifecycle import (
    AttemptNumber,
    RunLifecycle,
    StageAttempt,
    StageLifecycle,
    deterministic_input_hash,
    deterministic_run_key,
)

NOW = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (RunStatus.PENDING, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.RETRYING),
        (RunStatus.RUNNING, RunStatus.BLOCKED),
        (RunStatus.RUNNING, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.FAILED),
        (RunStatus.RUNNING, RunStatus.CANCELLED),
        (RunStatus.RETRYING, RunStatus.RUNNING),
        (RunStatus.RETRYING, RunStatus.FAILED),
        (RunStatus.BLOCKED, RunStatus.RUNNING),
        (RunStatus.BLOCKED, RunStatus.FAILED),
    ],
)
def test_run_lifecycle_accepts_declared_transitions(start: RunStatus, target: RunStatus) -> None:
    assert RunLifecycle(start).transition(target).status is target


@pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED])
def test_run_terminal_states_are_closed(terminal: RunStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        _ = RunLifecycle(terminal).transition(RunStatus.RUNNING)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (StageStatus.PENDING, StageStatus.RUNNING),
        (StageStatus.PENDING, StageStatus.SKIPPED),
        (StageStatus.PENDING, StageStatus.BLOCKED),
        (StageStatus.RUNNING, StageStatus.RETRYING),
        (StageStatus.RUNNING, StageStatus.COMPLETED),
        (StageStatus.RUNNING, StageStatus.FAILED),
        (StageStatus.RUNNING, StageStatus.BLOCKED),
        (StageStatus.RUNNING, StageStatus.CANCELLED),
        (StageStatus.RETRYING, StageStatus.RUNNING),
        (StageStatus.RETRYING, StageStatus.FAILED),
        (StageStatus.BLOCKED, StageStatus.RUNNING),
        (StageStatus.BLOCKED, StageStatus.SKIPPED),
        (StageStatus.BLOCKED, StageStatus.FAILED),
    ],
)
def test_stage_lifecycle_accepts_declared_transitions(
    start: StageStatus, target: StageStatus
) -> None:
    assert StageLifecycle(start).transition(target).status is target


def test_malformed_transition_reports_typed_context() -> None:
    with pytest.raises(InvalidTransitionError) as caught:
        _ = StageLifecycle(StageStatus.PENDING).transition(StageStatus.COMPLETED)
    assert caught.value.current == "pending"
    assert caught.value.target == "completed"


def test_attempt_requires_timezone_and_positive_number() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _ = StageAttempt(
            run_key="run",
            stage="01",
            attempt=AttemptNumber(1),
            status=StageStatus.RUNNING,
            started_at=datetime(2026, 7, 13, 4, 0, tzinfo=UTC).replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="positive"):
        _ = AttemptNumber(0)


def test_deterministic_keys_are_stable_for_equivalent_input() -> None:
    cycle = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)
    assert deterministic_run_key(" nvda ", cycle) == deterministic_run_key("NVDA", cycle)
    assert deterministic_input_hash('{"b":2,"a":1}') == deterministic_input_hash(
        '{ "a": 1, "b": 2 }'
    )


def test_transition_uses_injected_clock_without_wall_time_flakiness() -> None:
    later = datetime(2026, 7, 13, 4, 1, tzinfo=UTC)
    lifecycle = RunLifecycle(RunStatus.PENDING, transitioned_at=NOW, clock=lambda: later)
    transitioned = lifecycle.transition(RunStatus.RUNNING)
    assert transitioned.transitioned_at == later


@pytest.mark.parametrize(
    "payload",
    ["NaN", "Infinity", "-Infinity", '{"nested":[1,{"score":NaN}]}'],
)
def test_deterministic_input_hash_rejects_non_finite_numbers(payload: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        _ = deterministic_input_hash(payload)
