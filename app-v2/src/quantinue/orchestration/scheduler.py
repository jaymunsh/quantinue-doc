"""Automatic cycle scheduler: periodic tick → due check → windowed slot trigger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import anyio
import structlog

from quantinue.core.market_calendar import Session
from quantinue.orchestration.slots import slot_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from quantinue.core.market_calendar import NyseCalendar
    from quantinue.orchestration.policy import DueRoleScheduler, Mvp2ScheduleConfig


class _LatestCycleSource(Protocol):
    async def latest_cycle_ts(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class TickDecision:
    """One tick's verdict, kept observable for logs and the admin API."""

    triggered: bool
    reason: str
    cycle_ts: datetime | None = None


class CycleScheduler:
    """Trigger idempotent pipeline cycles while a trading session is open."""

    def __init__(
        self,
        config: Mvp2ScheduleConfig,
        calendar: NyseCalendar,
        scheduler: DueRoleScheduler,
        store: _LatestCycleSource,
        trigger: Callable[[datetime], bool],
    ) -> None:
        """Bind collaborators; the trigger owns ticker/request construction."""
        self._config = config
        self._calendar = calendar
        self._scheduler = scheduler
        self._store = store
        self._trigger = trigger
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("scheduler")

    async def tick(self, now: datetime) -> TickDecision:
        """Decide and, when due inside an allowed session, trigger one cycle."""
        if not self._config.enabled:
            return TickDecision(triggered=False, reason="disabled")
        normalized = now.astimezone(UTC)
        if not self._calendar.is_trading_day(normalized.date()):
            return TickDecision(triggered=False, reason="holiday")
        session = self._calendar.current_session(normalized)
        if session is Session.CLOSED or session.value not in self._config.trigger_sessions:
            return TickDecision(triggered=False, reason="closed_session")
        latest = await self._store.latest_cycle_ts()
        if latest is not None:
            last_runs = {role: latest for role, _ in self._scheduler.plan_periods()}
            if not self._scheduler.due_roles(normalized, last_runs):
                return TickDecision(triggered=False, reason="not_due")
        cycle_ts = slot_of(normalized, self._config.cycle_slot_minutes)
        self._trigger(cycle_ts)
        return TickDecision(triggered=True, reason="due", cycle_ts=cycle_ts)

    async def run_forever(self) -> None:
        """Tick forever; a failing tick is logged and never kills the loop."""
        while True:
            try:
                decision = await self.tick(datetime.now(UTC))
                if decision.triggered:
                    await self._logger.ainfo(
                        "scheduler.cycle.triggered", cycle_ts=str(decision.cycle_ts)
                    )
            except Exception:  # noqa: BLE001 — 루프 생존이 우선
                await self._logger.aexception("scheduler.tick.failed")
            await anyio.sleep(self._config.tick_seconds)
