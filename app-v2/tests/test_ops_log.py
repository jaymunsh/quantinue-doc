"""The operations log: every day at a glance, retries counted honestly.

관제실은 슬롯 하나를 깊게 본다. 이 페이지는 반대다 — 여러 날을 한 화면에
펼쳐 "매일 돌았나, 몇 번 돌았나, 안내는 나갔나"를 답한다. 숫자는 전부
``tb_job_run``이 답할 수 있는 것만이다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from quantinue.api.ops_log import build_ops_log
from quantinue.core.config import Settings
from quantinue.db.control_room_reads import JobRunRecord
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app

_TODAY = date(2026, 7, 21)
_YESTERDAY = date(2026, 7, 20)
_START = datetime(2026, 7, 21, 13, 0, tzinfo=UTC)


def _job(
    name: str,
    slot: date,
    *,
    status: str = "succeeded",
    attempts: int = 1,
    detail: str | None = "ok",
) -> JobRunRecord:
    return JobRunRecord(
        job_name=name,
        slot_date=slot,
        status=status,
        detail=detail,
        started_at=_START,
        finished_at=_START + timedelta(seconds=30) if status != "running" else None,
        attempts=attempts,
    )


class _Reads:
    def __init__(self, runs_by_slot: dict[date, tuple[JobRunRecord, ...]]) -> None:
        self._runs = runs_by_slot

    async def recent_job_slots(self, *, limit: int) -> tuple[date, ...]:
        assert limit > 0
        return tuple(sorted(self._runs, reverse=True))[:limit]

    async def job_runs(self, slot_date: date) -> tuple[JobRunRecord, ...]:
        return self._runs.get(slot_date, ())


@pytest.mark.anyio
async def test_each_day_summarises_its_chain_and_notification() -> None:
    reads = _Reads(
        {
            _TODAY: (
                _job("universe", _TODAY),
                _job("news", _TODAY, status="failed", attempts=2, detail="boom"),
                _job("daily_summary", _TODAY),
            ),
            _YESTERDAY: (
                _job("universe", _YESTERDAY),
                _job("exits", _YESTERDAY),
            ),
        }
    )

    view = await build_ops_log(reads)

    assert [slot.slot_date for slot in view.slots] == [_TODAY, _YESTERDAY]
    today = view.slots[0]
    assert (today.succeeded, today.failed) == (2, 1)
    # 재시도가 있었던 잡 수 — "하루에 여러 번 돌았다"를 원장 숫자로 말한다.
    assert today.retried_jobs == 1
    assert today.summary_sent is True
    yesterday = view.slots[1]
    assert yesterday.summary_sent is False
    assert yesterday.retried_jobs == 0


@pytest.mark.anyio
async def test_an_installation_with_no_runs_shows_an_empty_log() -> None:
    view = await build_ops_log(_Reads({}))

    assert view.slots == ()


class _LedgerStore(InMemoryRunStore):
    def __init__(self, reads: _Reads) -> None:
        super().__init__()
        self.domain = reads


def test_the_log_page_renders_each_day_with_its_attempt_count() -> None:
    reads = _Reads(
        {
            _TODAY: (
                _job("universe", _TODAY),
                _job("news", _TODAY, status="failed", attempts=3, detail="boom"),
            )
        }
    )
    settings = Settings(app_name="Quantinue Test")
    client = TestClient(create_app(settings, store=_LedgerStore(reads)))

    response = client.get("/admin/logs")

    assert response.status_code == 200
    body = response.text
    assert "2026-07-21" in body
    assert "3회" in body  # 재시도가 화면에 명확히 남는다
    assert "boom" in body
