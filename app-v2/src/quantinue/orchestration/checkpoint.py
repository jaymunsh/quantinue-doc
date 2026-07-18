"""Immutable checkpoint and deterministic resume contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from quantinue.core.errors import CheckpointSequenceError

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One atomically completed stage boundary."""

    run_key: str
    stage: str
    ordinal: int
    input_hash: str
    completed_at: datetime

    def __post_init__(self) -> None:
        """Reject malformed checkpoint identity and time."""
        if self.ordinal < 0:
            reason = "ordinal must be non-negative"
            raise CheckpointSequenceError(reason)
        if self.completed_at.tzinfo is None:
            reason = "completed_at must include a timezone"
            raise CheckpointSequenceError(reason)


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """The already-completed prefix and first safe stage to execute."""

    next_stage: str | None
    completed_stages: tuple[str, ...]


def resume_from(
    run_key: str, stages: tuple[str, ...], checkpoints: tuple[Checkpoint, ...]
) -> ResumePlan:
    """Parse checkpoints into one contiguous prefix or reject stale/corrupt state."""
    completed: list[str] = []
    for expected_ordinal, checkpoint in enumerate(checkpoints):
        if checkpoint.run_key != run_key:
            reason = f"checkpoint run key does not match requested run {run_key}"
            raise CheckpointSequenceError(reason)
        if expected_ordinal >= len(stages):
            reason = "more checkpoints than configured stages"
            raise CheckpointSequenceError(reason)
        expected_stage = stages[expected_ordinal]
        if checkpoint.ordinal != expected_ordinal or checkpoint.stage != expected_stage:
            reason = f"expected stage {expected_stage} at ordinal {expected_ordinal}"
            raise CheckpointSequenceError(reason)
        completed.append(checkpoint.stage)
    next_stage = stages[len(completed)] if len(completed) < len(stages) else None
    return ResumePlan(next_stage=next_stage, completed_stages=tuple(completed))
