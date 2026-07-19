"""Phase 2: the job run ledger — one durable row per job per day."""

from __future__ import annotations

import os
from datetime import date

import pytest

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
