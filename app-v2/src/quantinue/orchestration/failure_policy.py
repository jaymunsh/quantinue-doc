"""Stable retry and persistence classification for role failures."""

from dataclasses import dataclass

import httpx2
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from quantinue.core.errors import (
    AuthenticationFailureError,
    HardRiskFailureError,
    HttpFailureError,
    MissingStageDataError,
    RetryExhaustedError,
    TradingDisabledError,
    TransientFailureError,
    ValidationFailureError,
)
from quantinue.db.store import AttemptFailure
from quantinue.orchestration.retry import is_transient


@dataclass(frozen=True, slots=True)
class FailureDecision:
    """Retry classification paired with redacted persistence data."""

    retryable: bool
    failure: AttemptFailure


def classify_failure(error: Exception) -> FailureDecision:
    """Normalize role/provider failures to redacted stable persistence fields."""
    if isinstance(error, TimeoutError):
        return _retry(AttemptFailure("timed_out", "ROLE_TIMEOUT", "role execution timed out"))
    if isinstance(error, TransientFailureError):
        return _retry(AttemptFailure("failed", "TRANSIENT_FAILURE", "temporary provider failure"))
    if isinstance(error, HttpFailureError):
        transient = is_transient(error)
        code = "TRANSIENT_HTTP_FAILURE" if transient else "HTTP_FAILURE"
        failure = AttemptFailure("failed", code, "upstream HTTP failure")
        return _retry(failure) if transient else _terminal(failure)
    if isinstance(error, (httpx2.TransportError, ConnectionError, OSError, SQLAlchemyError)):
        return _classify_io_failure(error)
    return _classify_terminal_failure(error)


def _classify_io_failure(error: Exception) -> FailureDecision:
    if isinstance(error, httpx2.TransportError):
        return _retry(AttemptFailure("failed", "TRANSPORT_FAILURE", "provider transport failed"))
    if isinstance(error, (ConnectionError, OSError)):
        return _retry(AttemptFailure("failed", "CONNECTION_FAILURE", "provider connection failed"))
    if isinstance(error, OperationalError):
        return _retry(
            AttemptFailure("failed", "PERSISTENCE_UNAVAILABLE", "persistence operation unavailable")
        )
    if isinstance(error, IntegrityError):
        return _terminal(
            AttemptFailure(
                "failed", "PERSISTENCE_CONFLICT", "persistence constraint rejected stage"
            )
        )
    return _terminal(
        AttemptFailure("failed", "PERSISTENCE_FAILURE", "persistence operation failed")
    )


def _classify_terminal_failure(error: Exception) -> FailureDecision:
    if isinstance(error, AuthenticationFailureError):
        failure = AttemptFailure(
            "failed", "AUTHENTICATION_FAILURE", "provider authentication failed"
        )
    elif isinstance(error, HardRiskFailureError):
        failure = AttemptFailure("failed", "HARD_RISK_FAILURE", "hard risk gate rejected execution")
    elif isinstance(error, (ValidationFailureError, ValidationError)):
        failure = AttemptFailure("failed", "VALIDATION_FAILURE", "role input or output was invalid")
    elif isinstance(error, MissingStageDataError):
        failure = AttemptFailure(
            "failed", "MISSING_STAGE_DATA", "required upstream stage data missing"
        )
    elif isinstance(error, TradingDisabledError):
        failure = AttemptFailure("failed", "TRADING_DISABLED", "broker submission disabled")
    elif isinstance(error, RetryExhaustedError):
        failure = AttemptFailure("failed", "RETRY_EXHAUSTED", "provider retry budget exhausted")
    else:
        failure = AttemptFailure("failed", "UNEXPECTED_ROLE_FAILURE", "unexpected role failure")
    return _terminal(failure)


def _retry(failure: AttemptFailure) -> FailureDecision:
    return FailureDecision(retryable=True, failure=failure)


def _terminal(failure: AttemptFailure) -> FailureDecision:
    return FailureDecision(retryable=False, failure=failure)
