"""Operational alerts: the app said it woke up, and stuck slots call for help.

이 알림들은 인스턴스 단위 opt-in(``ops_alerts``)이다 — 코드 작업용(8021)이
같은 .env 키로 뜨는데, --reload가 재기동할 때마다 텔레그램이 울리면
진짜 신호가 소음에 묻힌다. 관측 인스턴스만 켠다(run_observation.sh).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.orchestration.policy import JobsConfig

# 2026-07-21 15:00 UTC = 뉴욕 11:00 (거래일 한복판)
_NOW = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
_SLOT = date(2026, 7, 21)


@dataclass
class _Row:
    job_name: str
    slot_date: date
    status: str
    detail: str | None
    started_at: datetime
    finished_at: datetime | None


class _Ledger:
    def __init__(
        self, rows: tuple[_Row, ...] = (), last_success: dict[str, date] | None = None
    ) -> None:
        self.rows = list(rows)
        self._last_success = last_success or {}

    async def reserve_job_run(self, job_name: str, slot_date: date) -> bool:
        return False  # 이 테스트의 관심은 실행이 아니라 알림이다

    async def finish_job_run(
        self, job_name: str, slot_date: date, *, succeeded: bool, detail: str | None = None
    ) -> None:
        return None

    async def last_job_success(self, job_name: str) -> date | None:
        return self._last_success.get(job_name)

    async def job_runs(self, slot_date: date) -> tuple[_Row, ...]:
        return tuple(row for row in self.rows if row.slot_date == slot_date)


class _Notify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


async def _noop(as_of: date) -> str:
    return "ok"


def _runner(ledger: _Ledger, notify: _Notify, *, ops_alerts: bool = True) -> JobRunner:
    return JobRunner(
        config=JobsConfig(enabled=True),
        ledger=ledger,
        jobs=(JobDefinition("universe", _noop), JobDefinition("exits", _noop)),
        notifier=notify,
        ops_alerts=ops_alerts,
    )


@pytest.mark.anyio
async def test_boot_notice_counts_what_is_still_due_today() -> None:
    # universe는 오늘 이미 성공, exits는 아직 — 대기 1개여야 한다.
    ledger = _Ledger(last_success={"universe": _SLOT})
    notify = _Notify()

    await _runner(ledger, notify).announce_boot(_NOW)

    assert len(notify.messages) == 1
    assert "앱 기동" in notify.messages[0]
    assert "2026-07-21" in notify.messages[0]
    assert "대기 잡 1/2" in notify.messages[0]


@pytest.mark.anyio
async def test_boot_notice_stays_quiet_unless_opted_in() -> None:
    notify = _Notify()

    await _runner(_Ledger(), notify, ops_alerts=False).announce_boot(_NOW)

    assert notify.messages == []


@pytest.mark.anyio
async def test_a_slot_stuck_in_running_is_reported_once() -> None:
    stuck = _Row(
        job_name="news",
        slot_date=_SLOT,
        status="running",
        detail=None,
        started_at=_NOW - timedelta(minutes=45),
        finished_at=None,
    )
    ledger = _Ledger(rows=[stuck])
    notify = _Notify()
    runner = _runner(ledger, notify)

    await runner.tick(_NOW)
    await runner.tick(_NOW + timedelta(minutes=1))  # 같은 굳음은 한 번만

    stuck_alerts = [m for m in notify.messages if "굳" in m]
    assert len(stuck_alerts) == 1
    assert "news" in stuck_alerts[0]
    assert "잠금 해제" in stuck_alerts[0]


@pytest.mark.anyio
async def test_a_recently_started_job_is_not_stuck() -> None:
    young = _Row(
        job_name="analysis:aggressive",
        slot_date=_SLOT,
        status="running",
        detail=None,
        started_at=_NOW - timedelta(minutes=10),
        finished_at=None,
    )
    notify = _Notify()

    await _runner(_Ledger(rows=[young]), notify).tick(_NOW)

    assert [m for m in notify.messages if "굳" in m] == []
