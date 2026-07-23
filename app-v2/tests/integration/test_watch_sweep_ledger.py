import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from sqlalchemy import text

from quantinue.db.domain import PostgresDomainRepository, WatchSweepStateError

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)


@pytest.fixture
async def domain() -> AsyncIterator[PostgresDomainRepository]:
    assert DATABASE_URL is not None
    repository = PostgresDomainRepository(DATABASE_URL)
    await repository.initialize()
    async with repository.engine.begin() as connection:
        _ = await connection.execute(text("TRUNCATE tb_watch_sweep"))
    yield repository
    await repository.close()


@pytest.mark.anyio
async def test_concurrent_claims_have_exactly_one_owner(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    outcomes: list[bool] = []

    async def claim() -> None:
        outcomes.append(
            (await domain.reserve_watch_sweep(sweep_at, now=sweep_at)) is not None
        )

    # When
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(claim)
        task_group.start_soon(claim)

    # Then
    assert sorted(outcomes) == [False, True]


@pytest.mark.anyio
async def test_stale_running_claim_is_recovered_and_attempt_increments(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    assert await domain.reserve_watch_sweep(sweep_at, now=sweep_at) == 1

    # When
    active = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=29))
    stale = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=31))

    # Then
    assert active is None
    assert stale == 2


@pytest.mark.anyio
async def test_terminal_write_without_running_ownership_fails(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)

    # When / Then
    with pytest.raises(WatchSweepStateError, match="transition lost"):
        await domain.finish_watch_sweep(
            sweep_at, attempt=1, succeeded=True, detail="targets=0", now=sweep_at
        )


@pytest.mark.anyio
async def test_reclaimed_generation_rejects_the_stale_owner_finish(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    owner_a = await domain.reserve_watch_sweep(sweep_at, now=sweep_at)
    owner_b = await domain.reserve_watch_sweep(
        sweep_at, now=sweep_at + timedelta(minutes=31)
    )
    assert owner_a == 1
    assert owner_b == 2

    # When / Then
    with pytest.raises(WatchSweepStateError, match="transition lost"):
        await domain.finish_watch_sweep(
            sweep_at,
            attempt=owner_a,
            succeeded=True,
            detail="stale owner",
            now=sweep_at + timedelta(minutes=32),
        )
    await domain.finish_watch_sweep(
        sweep_at,
        attempt=owner_b,
        succeeded=True,
        detail="current owner",
        now=sweep_at + timedelta(minutes=32),
    )


@pytest.mark.anyio
async def test_terminal_success_blocks_a_restart_claim(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    attempt = await domain.reserve_watch_sweep(sweep_at, now=sweep_at)
    assert attempt == 1
    await domain.finish_watch_sweep(
        sweep_at,
        attempt=attempt,
        succeeded=True,
        detail="analysis=1 order=1",
        now=sweep_at + timedelta(minutes=1),
    )

    # When
    restarted = await domain.reserve_watch_sweep(
        sweep_at, now=sweep_at + timedelta(hours=1)
    )

    # Then
    assert restarted is None
    async with domain.engine.begin() as connection:
        row = (
            await connection.execute(
                text("SELECT status, detail FROM tb_watch_sweep WHERE sweep_at=:sweep_at"),
                {"sweep_at": sweep_at},
            )
        ).one()
    assert tuple(row) == ("succeeded", "analysis=1 order=1")


@pytest.mark.anyio
async def test_terminal_failure_is_retryable_by_the_next_generation(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 16, 45, tzinfo=UTC)
    attempt = await domain.reserve_watch_sweep(sweep_at, now=sweep_at)
    assert attempt == 1
    await domain.finish_watch_sweep(
        sweep_at,
        attempt=attempt,
        succeeded=False,
        detail="partial",
        now=sweep_at + timedelta(minutes=1),
    )

    # When
    retry = await domain.reserve_watch_sweep(
        sweep_at, now=sweep_at + timedelta(minutes=2)
    )

    # Then
    assert retry == 2


@pytest.mark.anyio
async def test_reclaimed_owner_cannot_renew_or_continue_downstream_work(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    owner_a = await domain.reserve_watch_sweep(sweep_at, now=sweep_at)
    assert owner_a == 1
    release_owner_a = anyio.Event()
    owner_a_finished = anyio.Event()
    analyzer_calls = 0
    order_calls = 0
    owner_b = 0
    renewed = False

    async def stale_owner() -> None:
        nonlocal analyzer_calls, order_calls
        await release_owner_a.wait()
        if await domain.renew_watch_sweep(
            sweep_at,
            attempt=owner_a,
            now=sweep_at + timedelta(minutes=32),
        ):
            analyzer_calls += 1
            order_calls += 1
        with pytest.raises(WatchSweepStateError, match="transition lost"):
            await domain.finish_watch_sweep(
                sweep_at,
                attempt=owner_a,
                succeeded=True,
                detail="stale",
                now=sweep_at + timedelta(minutes=32),
            )
        owner_a_finished.set()

    # When
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stale_owner)
        claimed = await domain.reserve_watch_sweep(
            sweep_at, now=sweep_at + timedelta(minutes=31)
        )
        assert claimed == 2
        owner_b = claimed
        renewed = await domain.renew_watch_sweep(
            sweep_at,
            attempt=owner_b,
            now=sweep_at + timedelta(minutes=31, seconds=1),
        )
        if renewed:
            analyzer_calls += 1
            order_calls += 1
        release_owner_a.set()
        await owner_a_finished.wait()

    # Then
    assert renewed is True
    assert (analyzer_calls, order_calls) == (1, 1)
    await domain.finish_watch_sweep(
        sweep_at,
        attempt=owner_b,
        succeeded=True,
        detail="current",
        now=sweep_at + timedelta(minutes=33),
    )
