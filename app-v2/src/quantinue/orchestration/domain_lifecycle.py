"""Optional domain-persistence lifecycle seam for pipeline stages."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from quantinue.core.contracts import PipelineContext


@runtime_checkable
class DomainLifecycle(Protocol):
    """Persist canonical domain state after a validated role result."""

    async def stage_completed(
        self,
        component: str,
        previous: PipelineContext,
        result: PipelineContext,
    ) -> PipelineContext:
        """Persist stage domain state and return context carrying canonical IDs."""
        ...


@dataclass(frozen=True, slots=True)
class NoopDomainLifecycle:
    """Default adapter preserving memory-only and existing callers."""

    async def stage_completed(
        self,
        component: str,
        previous: PipelineContext,
        result: PipelineContext,
    ) -> PipelineContext:
        """Return the validated role result unchanged."""
        del component, previous
        return result
