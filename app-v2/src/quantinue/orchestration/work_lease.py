"""Renewable ownership contract for long-running intraday work."""

from typing import Protocol


class WorkLease(Protocol):
    """Fence paid calls and order effects to the current sweep generation."""

    async def renew(self) -> None:
        """Renew ownership or raise when this worker was superseded."""
        ...
