"""Automatic cycle scheduler: due + session window + slot idempotency."""

from datetime import UTC, datetime

import pytest

from quantinue.core.market_calendar import NyseCalendar
from quantinue.orchestration.policy import (
    DueRoleScheduler,
    Mvp2ScheduleConfig,
    default_schedule_plan,
)
from quantinue.orchestration.scheduler import CycleScheduler

MONDAY_REGULAR = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)  # 10:00 EDT Monday
SATURDAY = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)


class _StoreStub:
    def __init__(self, latest: datetime | None) -> None:
        self._latest = latest

    async def latest_cycle_ts(self) -> datetime | None:
        return self._latest


class _TriggerSpy:
    def __init__(self) -> None:
        self.cycles: list[datetime] = []

    def __call__(self, cycle_ts: datetime) -> bool:
        self.cycles.append(cycle_ts)
        return True


def _scheduler(
    latest: datetime | None, *, enabled: bool = True
) -> tuple[CycleScheduler, _TriggerSpy]:
    spy = _TriggerSpy()
    instance = CycleScheduler(
        config=Mvp2ScheduleConfig(enabled=enabled),
        calendar=NyseCalendar(),
        scheduler=DueRoleScheduler(default_schedule_plan()),
        store=_StoreStub(latest),
        trigger=spy,
    )
    return instance, spy


@pytest.mark.anyio
async def test_first_tick_with_no_history_triggers_catchup() -> None:
    scheduler, spy = _scheduler(latest=None)

    decision = await scheduler.tick(MONDAY_REGULAR)

    assert decision.triggered is True
    assert decision.reason == "due"
    assert spy.cycles[0] == datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_recent_run_is_not_due() -> None:
    scheduler, spy = _scheduler(latest=datetime(2026, 7, 20, 13, 50, tzinfo=UTC))

    decision = await scheduler.tick(MONDAY_REGULAR)

    assert decision.triggered is False
    assert decision.reason == "not_due"
    assert spy.cycles == []


@pytest.mark.anyio
async def test_stale_run_is_due_again() -> None:
    scheduler, spy = _scheduler(latest=datetime(2026, 7, 20, 13, 0, tzinfo=UTC))

    decision = await scheduler.tick(datetime(2026, 7, 20, 14, 31, tzinfo=UTC))

    assert decision.triggered is True  # role_06 cadence(30m) exceeded
    assert spy.cycles[0] == datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


@pytest.mark.anyio
async def test_weekend_never_triggers() -> None:
    scheduler, spy = _scheduler(latest=None)

    decision = await scheduler.tick(SATURDAY)

    assert decision.triggered is False
    assert decision.reason == "holiday"
    assert spy.cycles == []


@pytest.mark.anyio
async def test_closed_session_on_trading_day_does_not_trigger() -> None:
    scheduler, spy = _scheduler(latest=None)
    night = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)  # 02:00 EDT Monday — before pre-session

    decision = await scheduler.tick(night)

    assert decision.triggered is False
    assert decision.reason == "closed_session"
    assert spy.cycles == []


@pytest.mark.anyio
async def test_disabled_scheduler_never_triggers() -> None:
    scheduler, spy = _scheduler(latest=None, enabled=False)

    decision = await scheduler.tick(MONDAY_REGULAR)

    assert decision.triggered is False
    assert decision.reason == "disabled"
    assert spy.cycles == []


@pytest.mark.anyio
async def test_same_slot_double_tick_sends_same_cycle_key() -> None:
    first_scheduler, first_spy = _scheduler(latest=None)
    second_scheduler, second_spy = _scheduler(latest=None)

    _ = await first_scheduler.tick(datetime(2026, 7, 20, 14, 1, tzinfo=UTC))
    _ = await second_scheduler.tick(datetime(2026, 7, 20, 14, 16, tzinfo=UTC))

    # 별개 프로세스/수동 발화가 같은 슬롯에서 나가도 cycle_ts 동일 → claim이 dedup.
    assert first_spy.cycles[0] == second_spy.cycles[0]
