"""Small type helpers used by role boundaries."""

from typing import TypeVar

from quantinue.core.errors import MissingStageDataError

T = TypeVar("T")


def require_value(value: T | None, *, component: str, field_name: str) -> T:
    """Narrow an optional upstream value or fail with context."""
    if value is None:
        raise MissingStageDataError(component=component, field_name=field_name)
    return value
