"""운영 기준 페이지 — 잡이 언제, 어떤 기준으로 도는가.

관제실은 "오늘 무슨 일이 있었나"를 답한다. 이 페이지는 그 앞의 질문에
답한다: **애초에 언제 돌기로 되어 있나.** 지금까지 그 답은 코드와 yaml에만
있었고, 화면만 보고는 알 수 없었다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from quantinue.api.schedule import build_schedule
from quantinue.core.config import Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app
from quantinue.orchestration.policy import JobCadenceConfig, JobsConfig

# 2026-07-21 04:00 UTC = 뉴욕 00:00 = 서울 13:00 — 슬롯이 막 바뀐 시각
_NOW = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)


class _Ledger:
    def __init__(self, last_success: dict[str, date] | None = None) -> None:
        self._last = last_success or {}

    async def last_job_success(self, job_name: str) -> date | None:
        return self._last.get(job_name)


@pytest.mark.anyio
async def test_each_job_reports_its_cadence_and_when_it_next_runs() -> None:
    config = JobsConfig(
        enabled=True,
        cadences={"universe": JobCadenceConfig(interval_days=7)},
    )
    ledger = _Ledger({"universe": date(2026, 7, 20), "daily_bars": date(2026, 7, 20)})

    view = await build_schedule(
        job_names=("universe", "daily_bars"), config=config, ledger=ledger, now=_NOW
    )

    universe, bars = view.jobs
    assert (universe.job_name, universe.interval_days) == ("universe", 7)
    assert universe.last_success == date(2026, 7, 20)
    # 주 1회 잡은 어제 성공했으니 아직 아니다
    assert universe.due_today is False
    assert universe.next_due == date(2026, 7, 27)
    # 일 1회 잡은 어제가 마지막이므로 오늘이다
    assert (bars.interval_days, bars.due_today) == (1, True)


@pytest.mark.anyio
async def test_a_job_that_never_ran_is_due_now() -> None:
    view = await build_schedule(
        job_names=("news",), config=JobsConfig(enabled=True), ledger=_Ledger(), now=_NOW
    )

    job = view.jobs[0]
    assert job.last_success is None
    assert job.due_today is True


@pytest.mark.anyio
async def test_the_slot_follows_the_new_york_date_not_the_local_one() -> None:
    """슬롯이 뉴욕 날짜라는 사실이 이 페이지의 존재 이유다."""
    # 서울 12:59 = 뉴욕 23:59 전날 — 아직 어제 슬롯이다
    before = await build_schedule(
        job_names=(),
        config=JobsConfig(enabled=True),
        ledger=_Ledger(),
        now=datetime(2026, 7, 21, 3, 59, tzinfo=UTC),
    )
    after = await build_schedule(
        job_names=(), config=JobsConfig(enabled=True), ledger=_Ledger(), now=_NOW
    )

    assert before.slot_date == date(2026, 7, 20)
    assert after.slot_date == date(2026, 7, 21)


@pytest.mark.anyio
async def test_a_holiday_reports_that_nothing_will_run() -> None:
    """휴장이면 잡이 안 도는 것이 정상이다 — 화면이 그걸 말해야 침묵이 안 무섭다."""
    saturday = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)  # 뉴욕 토요일 정오

    view = await build_schedule(
        job_names=("universe",), config=JobsConfig(enabled=True), ledger=_Ledger(), now=saturday
    )

    assert view.is_trading_day is False


class _Store(InMemoryRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.domain = _Ledger({"universe": date(2026, 7, 20)})


def test_the_schedule_page_explains_the_clock_and_lists_the_jobs() -> None:
    client = TestClient(create_app(Settings(app_name="Quantinue Test"), store=_Store()))

    response = client.get("/admin/schedule")

    assert response.status_code == 200
    body = response.text
    assert "뉴욕" in body  # 슬롯 기준이 무엇인지
    assert "13:00" in body  # 서울에서 하루가 바뀌는 시각
    # "매일"은 하루 몇 번인지 말하지 않는다 — 1회 보장이 화면에 있어야 한다.
    assert "거래일마다 1회" in body
