"""Closed run and stage lifecycle value contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, NewType

from pydantic import TypeAdapter
from typing_extensions import TypeAliasType

from quantinue.core.contracts import RunStatus, StageStatus
from quantinue.core.errors import InvalidTransitionError, NonFiniteInputError

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current UTC instant through the injectable clock seam."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AttemptNumber:
    """One-based execution attempt number."""

    value: int

    def __post_init__(self) -> None:
        """Reject non-positive attempt identities."""
        if self.value < 1:
            msg = "attempt number must be positive"
            raise ValueError(msg)


InputHash = NewType("InputHash", str)
RunKey = NewType("RunKey", str)
JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)
JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)

RUN_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.RETRYING,
            RunStatus.BLOCKED,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.RETRYING: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.BLOCKED: frozenset({RunStatus.RUNNING, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

STAGE_TRANSITIONS: Final[dict[StageStatus, frozenset[StageStatus]]] = {
    StageStatus.PENDING: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED, StageStatus.BLOCKED}),
    StageStatus.RUNNING: frozenset(
        {
            StageStatus.RETRYING,
            StageStatus.COMPLETED,
            StageStatus.FAILED,
            StageStatus.BLOCKED,
            StageStatus.CANCELLED,
        }
    ),
    StageStatus.RETRYING: frozenset({StageStatus.RUNNING, StageStatus.FAILED}),
    StageStatus.BLOCKED: frozenset({StageStatus.RUNNING, StageStatus.SKIPPED, StageStatus.FAILED}),
    StageStatus.COMPLETED: frozenset(),
    StageStatus.FAILED: frozenset(),
    StageStatus.SKIPPED: frozenset(),
    StageStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RunLifecycle:
    """Immutable run state with a closed transition graph."""

    status: RunStatus
    transitioned_at: datetime = field(default_factory=utc_now)
    clock: Clock = field(default=utc_now, repr=False, compare=False)

    def transition(self, target: RunStatus) -> RunLifecycle:
        """Return the target state when the graph permits it."""
        if target not in RUN_TRANSITIONS[self.status]:
            lifecycle = "run"
            raise InvalidTransitionError(lifecycle, self.status, target)
        return RunLifecycle(target, self.clock(), self.clock)


@dataclass(frozen=True, slots=True)
class StageLifecycle:
    """Immutable stage state with a closed transition graph."""

    status: StageStatus
    transitioned_at: datetime = field(default_factory=utc_now)
    clock: Clock = field(default=utc_now, repr=False, compare=False)

    def transition(self, target: StageStatus) -> StageLifecycle:
        """Return the target state when the graph permits it."""
        if target not in STAGE_TRANSITIONS[self.status]:
            lifecycle = "stage"
            raise InvalidTransitionError(lifecycle, self.status, target)
        return StageLifecycle(target, self.clock(), self.clock)


@dataclass(frozen=True, slots=True)
class StageAttempt:
    """Persistable observation of one stage execution attempt."""

    run_key: str
    stage: str
    attempt: AttemptNumber
    status: StageStatus
    started_at: datetime
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        """Require a coherent timezone-aware attempt interval."""
        if self.started_at.tzinfo is None:
            msg = "started_at must include a timezone"
            raise ValueError(msg)
        if self.finished_at is not None and self.finished_at.tzinfo is None:
            msg = "finished_at must include a timezone"
            raise ValueError(msg)
        if self.finished_at is not None and self.finished_at < self.started_at:
            msg = "finished_at must not precede started_at"
            raise ValueError(msg)


def deterministic_run_key(ticker: str, cycle_ts: datetime) -> RunKey:
    """Derive the stable execution identity from symbol and planned slot."""
    if cycle_ts.tzinfo is None:
        msg = "cycle_ts must include a timezone"
        raise ValueError(msg)
    normalized = f"{ticker.strip().upper()}:{cycle_ts.astimezone(UTC).isoformat()}"
    return RunKey(hashlib.sha256(normalized.encode()).hexdigest())


def deterministic_input_hash(serialized_input: str) -> InputHash:
    """Hash canonical JSON so key ordering and whitespace cannot cause drift."""
    parsed = JSON_ADAPTER.validate_json(serialized_input)
    try:
        canonical = json.dumps(
            parsed,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except ValueError as error:
        raise NonFiniteInputError from error
    return InputHash(hashlib.sha256(canonical.encode()).hexdigest())
