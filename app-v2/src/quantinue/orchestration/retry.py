"""Transient-only bounded retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, assert_never

from quantinue.core.errors import (
    AuthenticationFailureError,
    HardRiskFailureError,
    HttpFailureError,
    TransientFailureError,
    ValidationFailureError,
)

SERVER_ERROR_STATUS: Final = 500
INVALID_HTTP_STATUS: Final = 600
KnownFailure = (
    ValidationFailureError
    | HardRiskFailureError
    | AuthenticationFailureError
    | HttpFailureError
    | TransientFailureError
    | TimeoutError
)


class Sleeper(Protocol):
    """Injected delay capability; production may implement it with AnyIO."""

    async def sleep(self, delay_seconds: float) -> None:
        """Wait without exposing an event-loop-specific primitive."""
        ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Finite exponential backoff without jitter for deterministic scheduling."""

    max_attempts: int
    initial_delay_seconds: float = 0.5
    multiplier: float = 2
    max_delay_seconds: float = 8

    def __post_init__(self) -> None:
        """Reject unbounded or negative policy values."""
        if self.max_attempts < 1:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        if self.initial_delay_seconds < 0 or self.multiplier < 1 or self.max_delay_seconds < 0:
            msg = "retry delays must be non-negative and multiplier at least one"
            raise ValueError(msg)

    def delay_after(self, attempt: int) -> float:
        """Return the bounded delay after a one-based failed attempt."""
        return min(
            self.initial_delay_seconds * self.multiplier ** (attempt - 1),
            self.max_delay_seconds,
        )


def is_transient(error: KnownFailure) -> bool:
    """Classify only explicitly temporary errors as retryable."""
    match error:
        case TimeoutError() | TransientFailureError():
            return True
        case HttpFailureError(status_code=status_code):
            return (
                status_code in {408, 425, 429}
                or SERVER_ERROR_STATUS <= status_code < INVALID_HTTP_STATUS
            )
        case ValidationFailureError() | HardRiskFailureError() | AuthenticationFailureError():
            return False
        case unreachable:
            assert_never(unreachable)
