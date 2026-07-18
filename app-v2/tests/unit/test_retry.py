import pytest

from quantinue.core.errors import (
    AuthenticationFailureError,
    HardRiskFailureError,
    HttpFailureError,
    TransientFailureError,
    ValidationFailureError,
)
from quantinue.orchestration.retry import KnownFailure, RetryPolicy, is_transient


@pytest.mark.parametrize(
    "error",
    [
        ValidationFailureError(field="score", reason="out of range"),
        HardRiskFailureError(rule="kill-switch"),
        AuthenticationFailureError(provider="alpaca"),
        HttpFailureError(status_code=400),
        HttpFailureError(status_code=404),
        HttpFailureError(status_code=700),
    ],
)
def test_non_transient_classifier(error: KnownFailure) -> None:
    assert not is_transient(error)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        TransientFailureError(provider="feed", reason="unavailable"),
        HttpFailureError(status_code=408),
        HttpFailureError(status_code=429),
        HttpFailureError(status_code=503),
    ],
)
def test_transient_classifier(error: KnownFailure) -> None:
    assert is_transient(error)


@pytest.mark.parametrize(("attempt", "expected"), [(1, 0.5), (2, 1.5), (3, 2.0), (20, 2.0)])
def test_backoff_is_bounded(attempt: int, expected: float) -> None:
    policy = RetryPolicy(
        max_attempts=20, initial_delay_seconds=0.5, multiplier=3, max_delay_seconds=2
    )
    assert policy.delay_after(attempt) == expected
