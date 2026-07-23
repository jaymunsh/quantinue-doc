"""Renewable ownership contract for long-running intraday work."""

from typing import Protocol


class WorkLease(Protocol):
    """Fence paid calls and order effects to the current sweep generation."""

    async def renew(self) -> None:
        """Renew ownership or raise when this worker was superseded."""
        ...

    async def claim_item(self, ticker: str, persona: str) -> bool:
        """Claim an undispatched persona-ticker item."""
        ...

    async def mark_dispatched(self, ticker: str, persona: str) -> None:
        """Persist the irreversible provider boundary."""
        ...

    async def complete_item(self, ticker: str, persona: str) -> None:
        """Record that the dispatched item completed locally."""
        ...

    async def release_item(self, ticker: str, persona: str) -> None:
        """Release only a claim that never reached provider dispatch."""
        ...
