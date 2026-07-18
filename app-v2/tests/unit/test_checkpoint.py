from datetime import UTC, datetime

import pytest

from quantinue.core.errors import CheckpointSequenceError
from quantinue.orchestration.checkpoint import Checkpoint, ResumePlan, resume_from

NOW = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)
STAGES = ("01", "02", "03", "04")


def test_resume_starts_after_last_contiguous_completed_checkpoint() -> None:
    plan = resume_from(
        "run",
        STAGES,
        (
            Checkpoint(run_key="run", stage="01", ordinal=0, input_hash="a", completed_at=NOW),
            Checkpoint(run_key="run", stage="02", ordinal=1, input_hash="b", completed_at=NOW),
        ),
    )
    assert plan == ResumePlan(next_stage="03", completed_stages=("01", "02"))


def test_repeated_interruption_does_not_repeat_completed_stage() -> None:
    checkpoints = (
        Checkpoint(run_key="run", stage="01", ordinal=0, input_hash="a", completed_at=NOW),
    )
    assert resume_from("run", STAGES, checkpoints) == resume_from("run", STAGES, checkpoints)
    assert resume_from("run", STAGES, checkpoints).next_stage == "02"


def test_all_completed_has_no_resume_stage() -> None:
    checkpoints = tuple(
        Checkpoint(
            run_key="run", stage=stage, ordinal=index, input_hash=str(index), completed_at=NOW
        )
        for index, stage in enumerate(STAGES)
    )
    assert resume_from("run", STAGES, checkpoints).next_stage is None


@pytest.mark.parametrize(
    "checkpoints",
    [
        (Checkpoint(run_key="run", stage="02", ordinal=1, input_hash="b", completed_at=NOW),),
        (
            Checkpoint(run_key="run", stage="01", ordinal=0, input_hash="a", completed_at=NOW),
            Checkpoint(run_key="run", stage="01", ordinal=0, input_hash="a", completed_at=NOW),
        ),
        (Checkpoint(run_key="run", stage="99", ordinal=0, input_hash="a", completed_at=NOW),),
    ],
)
def test_malformed_checkpoint_sequence_is_rejected(
    checkpoints: tuple[Checkpoint, ...],
) -> None:
    with pytest.raises(CheckpointSequenceError):
        _ = resume_from("run", STAGES, checkpoints)


def test_mixed_run_checkpoints_cannot_advance_requested_run() -> None:
    checkpoints = (
        Checkpoint(run_key="run-a", stage="01", ordinal=0, input_hash="a", completed_at=NOW),
        Checkpoint(run_key="run-b", stage="02", ordinal=1, input_hash="b", completed_at=NOW),
    )
    with pytest.raises(CheckpointSequenceError, match="run key"):
        _ = resume_from("run-a", STAGES, checkpoints)
