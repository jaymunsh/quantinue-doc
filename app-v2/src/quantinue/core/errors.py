"""Application-specific typed errors."""

from dataclasses import dataclass

from typing_extensions import override


@dataclass(frozen=True, slots=True)
class MissingStageDataError(Exception):
    """A role ran before a required upstream value existed."""

    component: str
    field_name: str

    @override
    def __str__(self) -> str:
        """Render the missing upstream contract."""
        return f"component {self.component} requires {self.field_name}"


@dataclass(frozen=True, slots=True)
class TradingDisabledError(Exception):
    """A real order was requested while the kill switch was active."""

    @override
    def __str__(self) -> str:
        """Render the active safety condition."""
        return "real broker submission is disabled"


@dataclass(frozen=True, slots=True)
class InvalidTransitionError(Exception):
    """A lifecycle transition is outside the closed transition graph."""

    lifecycle: str
    current: str
    target: str

    @override
    def __str__(self) -> str:
        return f"invalid {self.lifecycle} transition: {self.current} -> {self.target}"


@dataclass(frozen=True, slots=True)
class ValidationFailureError(Exception):
    """Boundary data failed a deterministic contract."""

    field: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"validation failed for {self.field}: {self.reason}"


@dataclass(frozen=True, slots=True)
class HardRiskFailureError(Exception):
    """A code-enforced risk gate stopped execution."""

    rule: str

    @override
    def __str__(self) -> str:
        return f"hard risk gate rejected execution: {self.rule}"


@dataclass(frozen=True, slots=True)
class AuthenticationFailureError(Exception):
    """A provider rejected configured credentials."""

    provider: str

    @override
    def __str__(self) -> str:
        return f"authentication failed for {self.provider}"


@dataclass(frozen=True, slots=True)
class HttpFailureError(Exception):
    """An upstream provider returned an HTTP failure."""

    status_code: int

    @override
    def __str__(self) -> str:
        return f"upstream HTTP failure: {self.status_code}"


@dataclass(frozen=True, slots=True)
class TransientFailureError(Exception):
    """A temporary provider condition may succeed on retry."""

    provider: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"temporary failure from {self.provider}: {self.reason}"


@dataclass(frozen=True, slots=True)
class RetryExhaustedError(Exception):
    """A retryable operation consumed its bounded attempt budget."""

    attempts: int
    last_error: (
        ValidationFailureError
        | HardRiskFailureError
        | AuthenticationFailureError
        | HttpFailureError
        | TransientFailureError
        | TimeoutError
    )

    @override
    def __str__(self) -> str:
        return f"retry budget exhausted after {self.attempts} attempts"


@dataclass(frozen=True, slots=True)
class CheckpointSequenceError(Exception):
    """Persisted checkpoints do not form one contiguous stage prefix."""

    reason: str

    @override
    def __str__(self) -> str:
        return f"invalid checkpoint sequence: {self.reason}"


@dataclass(frozen=True, slots=True)
class NonFiniteInputError(ValueError):
    """Canonical input contains a JSON number without a finite value."""

    @override
    def __str__(self) -> str:
        return "canonical input numbers must be finite"
