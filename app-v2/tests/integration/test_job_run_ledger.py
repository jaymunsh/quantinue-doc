"""Phase 2: the job run ledger — one durable row per job per day."""

from __future__ import annotations

import os
from datetime import date, datetime

import pytest

from quantinue.db.control_room_reads import job_runs
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 9)


@pytest.mark.anyio
async def test_a_slot_can_be_reserved_once_and_only_once() -> None:
    """같은 날 두 번 트리거돼도 잡 본문은 한 번만 돈다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    first = await store.domain.reserve_job_run("ledger-once", _DAY)
    second = await store.domain.reserve_job_run("ledger-once", _DAY)

    # Then
    assert first is True
    assert second is False
    await store.close()


@pytest.mark.anyio
async def test_a_retry_is_counted_so_the_day_shows_how_many_times_it_ran() -> None:
    """"하루에 몇 번 돌았나"는 원장이 답해야 한다 — 화면이 지어내면 안 된다."""
    # Given: 첫 시도가 실패로 끝났다
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    day = date(2026, 7, 11)
    assert await store.domain.reserve_job_run("ledger-attempts", day) is True
    await store.domain.finish_job_run("ledger-attempts", day, succeeded=False, detail="boom")

    # When: 같은 날 재시도가 슬롯을 다시 집는다
    assert await store.domain.reserve_job_run("ledger-attempts", day) is True
    await store.domain.finish_job_run("ledger-attempts", day, succeeded=True, detail="ok")

    # Then: 시도 횟수가 2로 남는다
    rows = await store.domain.job_runs(day)
    row = next(item for item in rows if item.job_name == "ledger-attempts")
    assert row.attempts == 2
    assert row.status == "succeeded"
    await store.close()


@pytest.mark.anyio
async def test_different_jobs_and_days_do_not_block_each_other() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-a", _DAY)

    # When
    other_job = await store.domain.reserve_job_run("ledger-b", _DAY)
    next_day = await store.domain.reserve_job_run("ledger-a", date(2026, 7, 10))

    # Then
    assert other_job is True
    assert next_day is True
    await store.close()


@pytest.mark.anyio
async def test_only_a_finished_success_counts_as_the_last_success() -> None:
    """예약만 하고 죽은 잡을 성공으로 세면, 그 주기를 통째로 잃는다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-success", _DAY)

    # When: 예약 직후 — 아직 끝나지 않았다
    while_running = await store.domain.last_job_success("ledger-success")
    await store.domain.finish_job_run("ledger-success", _DAY, succeeded=True)
    after_success = await store.domain.last_job_success("ledger-success")

    # Then
    assert while_running is None
    assert after_success == _DAY
    await store.close()


@pytest.mark.anyio
async def test_a_failed_run_does_not_advance_the_last_success() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-failed", _DAY)
    await store.domain.finish_job_run(
        "ledger-failed", _DAY, succeeded=False, detail="boom"
    )

    # When
    last = await store.domain.last_job_success("ledger-failed")

    # Then
    assert last is None
    await store.close()


@pytest.mark.anyio
async def test_last_success_of_an_unknown_job_is_absent() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    last = await store.domain.last_job_success("ledger-never-ran")

    # Then
    assert last is None
    await store.close()


@pytest.mark.anyio
async def test_a_failed_slot_can_be_reclaimed_the_same_day() -> None:
    """수집이 한 번 실패했다고 하루를 묵은 봉으로 보내면 안 된다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-retry", _DAY)
    await store.domain.finish_job_run(
        "ledger-retry", _DAY, succeeded=False, detail="transient"
    )

    # When
    retried = await store.domain.reserve_job_run("ledger-retry", _DAY)
    await store.domain.finish_job_run("ledger-retry", _DAY, succeeded=True)

    # Then
    assert retried is True
    assert await store.domain.last_job_success("ledger-retry") == _DAY
    await store.close()


@pytest.mark.anyio
async def test_a_reclaimed_slot_restarts_its_clock() -> None:
    """재시도한 잡의 시계는 다시 0에서 출발해야 한다.

    ``started_at``이 첫 시도 값으로 남으면 관제실이 두 가지를 동시에 잃는다:
    소요시간이 실패와 재시도 사이의 공백을 포함해 부풀고(실측 14.5초짜리
    뉴스 잡이 9.8시간으로 찍혔다), 체인을 ``started_at``으로 정렬하는
    관제실이 등록 순서를 잘못 그린다 — 어느 단계에서 끊겼는지 못 읽는다.
    """
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-clock", _DAY)
    first_started = (await _started_at(store, "ledger-clock"))
    await store.domain.finish_job_run("ledger-clock", _DAY, succeeded=False)

    # When
    _ = await store.domain.reserve_job_run("ledger-clock", _DAY)

    # Then
    assert await _started_at(store, "ledger-clock") > first_started
    await store.close()


async def _started_at(store: PostgresRunStore, job_name: str) -> datetime:
    """Read one job's clock through the control room's own reader."""
    runs = await job_runs(store.engine, _DAY)
    return next(run.started_at for run in runs if run.job_name == job_name)


@pytest.mark.anyio
async def test_a_succeeded_slot_is_never_reclaimed() -> None:
    """재시도를 여는 것이 성공까지 다시 열어서는 안 된다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-done", _DAY)
    await store.domain.finish_job_run("ledger-done", _DAY, succeeded=True)

    # When
    again = await store.domain.reserve_job_run("ledger-done", _DAY)

    # Then
    assert again is False
    await store.close()


@pytest.mark.anyio
async def test_a_running_slot_is_never_reclaimed() -> None:
    """아직 도는 잡을 다시 집으면 같은 날 두 번 돈다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-running", _DAY)

    # When
    again = await store.domain.reserve_job_run("ledger-running", _DAY)

    # Then
    assert again is False
    await store.close()


@pytest.mark.anyio
async def test_only_a_stuck_run_can_be_released() -> None:
    """수동 운영에서 앱을 잡 도중에 끄면 슬롯이 running으로 굳는다.

    재시도 갈래가 ``failed``만 집으므로 그 슬롯은 그날 영영 안 돈다. 해제는
    잡을 실행하지 않고 잠금만 푼다 — 러너가 다음 틱에 스스로 다시 집는다.
    """
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    _ = await store.domain.reserve_job_run("ledger-stuck", _DAY)
    _ = await store.domain.reserve_job_run("ledger-finished", _DAY)
    await store.domain.finish_job_run("ledger-finished", _DAY, succeeded=True)

    # When
    released = await store.domain.release_job_slot("ledger-stuck", _DAY)
    refused = await store.domain.release_job_slot("ledger-finished", _DAY)
    reclaimed = await store.domain.reserve_job_run("ledger-stuck", _DAY)

    # Then
    assert released is True
    # 성공한 슬롯을 다시 열면 같은 날 두 번 돈다 — 배분에는 같은 후보를 두 번 사는 길이다.
    assert refused is False
    assert reclaimed is True
    await store.close()
