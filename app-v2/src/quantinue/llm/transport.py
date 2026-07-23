"""Transport-error classification shared by LLM adapters."""

import httpx
from openai import APIConnectionError, APITimeoutError
from pydantic_ai.exceptions import ModelAPIError


def has_transient_transport_cause(error: ModelAPIError) -> bool:
    """Report whether a model failure wraps a retryable transport error."""
    current: BaseException | None = error.__cause__
    while current is not None:
        if isinstance(
            current,
            (
                APITimeoutError,
                APIConnectionError,
                httpx.TimeoutException,
                httpx.TransportError,
                TimeoutError,
            ),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
