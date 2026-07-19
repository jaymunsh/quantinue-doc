"""Application-lifetime asynchronous pipeline ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from quantinue.orchestration.lifecycle import deterministic_run_key

if TYPE_CHECKING:
    from anyio.abc import TaskGroup

    from quantinue.core.contracts import PipelineRequest
    from quantinue.orchestration.pipeline import PipelineOrchestrator


class LiveRunRuntime:
    """Own at most one task-group child for each deterministic run key."""

    def __init__(self, task_group: TaskGroup, orchestrator: PipelineOrchestrator) -> None:
        """Bind the application task group and its pipeline runner."""
        self._task_group = task_group
        self._orchestrator = orchestrator
        self._keys: set[str] = set()
        self._completed_screenings: set[str] = set()
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("live-run")

    def start(self, request: PipelineRequest) -> bool:
        """Schedule a run and report whether this runtime acquired ownership."""
        key = str(deterministic_run_key(request.ticker, request.cycle_ts))
        if key in self._keys:
            return False
        self._keys.add(key)
        _ = self._task_group.start_soon(self._execute, key, request)
        return True

    def start_screening(self, request: PipelineRequest) -> bool:
        """Schedule one automatic screening cycle."""
        key = f"screening:{request.cycle_ts.isoformat()}"
        if (
            key in self._keys
            or key in self._completed_screenings
            or any(item.startswith("screening:") for item in self._keys)
        ):
            return False
        self._keys.add(key)
        _ = self._task_group.start_soon(self._execute_screening, key, request)
        return True

    def cancel(self) -> None:
        """Cancel children; the enclosing task group awaits their cleanup."""
        self._task_group.cancel_scope.cancel()

    async def _execute(self, key: str, request: PipelineRequest) -> None:
        try:
            _ = await self._orchestrator.run(request)
        except Exception:  # noqa: BLE001 - app-lifetime boundary must isolate a failed run.
            await self._logger.aexception("live_run.failed", run_key=key)
        finally:
            self._keys.discard(key)

    async def _execute_screening(self, key: str, request: PipelineRequest) -> None:
        try:
            _ = await self._orchestrator.run_screening(request)
            self._completed_screenings.add(key)
        except Exception:  # noqa: BLE001 - app-lifetime boundary isolates batch failure.
            await self._logger.aexception("live_screening.failed", screening_key=key)
        finally:
            self._keys.discard(key)
