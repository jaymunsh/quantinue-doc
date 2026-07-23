import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import anyio
import pytest
from sqlalchemy import text
from typing_extensions import override

from quantinue.db.domain import PostgresDomainRepository, WatchSweepStateError
from quantinue.market_data.models import LatestTrade
from quantinue.orchestration.policy import RejudgeConfig, WatchConfig
from quantinue.orchestration.watch_runner import WatchRunner
from quantinue.orchestration.work_lease import WorkLease
from quantinue.roles.exits import ExitDecision, OpenPosition

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
        _ = await connection.execute(text("TRUNCATE tb_watch_sweep_item, tb_watch_sweep"))
    yield repository
    await repository.close()


class _RunnerDomain:
    def __init__(self, repository: PostgresDomainRepository) -> None:
        self.repository = repository

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return (
            OpenPosition(
                order_id=1,
                signal_id=1,
                account_id=1,
                ticker="NVDA",
                quantity=1,
                entry_price=Decimal(100),
                stop_price=None,
                take_profit_price=None,
                filled_on=date(2026, 7, 1),
            ),
        )

    async def reference_closes(
        self, tickers: tuple[str, ...], *, before: date
    ) -> dict[str, Decimal]:
        _ = before
        return dict.fromkeys(tickers, Decimal(100))

    async def reserve_watch_sweep(self, sweep_at: datetime, *, now: datetime) -> int | None:
        return await self.repository.reserve_watch_sweep(sweep_at, now=now)

    async def finish_watch_sweep(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        succeeded: bool,
        detail: str,
        now: datetime,
    ) -> None:
        await self.repository.finish_watch_sweep(
            sweep_at,
            attempt=attempt,
            succeeded=succeeded,
            detail=detail,
            now=now,
        )

    async def renew_watch_sweep(self, sweep_at: datetime, *, attempt: int, now: datetime) -> bool:
        return await self.repository.renew_watch_sweep(sweep_at, attempt=attempt, now=now)

    async def claim_watch_sweep_item(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        ticker: str,
        persona: str,
        now: datetime,
    ) -> bool:
        return await self.repository.claim_watch_sweep_item(
            sweep_at,
            attempt=attempt,
            ticker=ticker,
            persona=persona,
            now=now,
        )

    async def dispatch_watch_sweep_item(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        ticker: str,
        persona: str,
        now: datetime,
    ) -> bool:
        return await self.repository.dispatch_watch_sweep_item(
            sweep_at,
            attempt=attempt,
            ticker=ticker,
            persona=persona,
            now=now,
        )

    async def complete_watch_sweep_item(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        ticker: str,
        persona: str,
        now: datetime,
    ) -> bool:
        return await self.repository.complete_watch_sweep_item(
            sweep_at,
            attempt=attempt,
            ticker=ticker,
            persona=persona,
            now=now,
        )

    async def release_watch_sweep_item(
        self,
        sweep_at: datetime,
        *,
        attempt: int,
        ticker: str,
        persona: str,
    ) -> None:
        await self.repository.release_watch_sweep_item(
            sweep_at,
            attempt=attempt,
            ticker=ticker,
            persona=persona,
        )


class _RunnerQuotes:
    async def latest_trades(self, tickers: tuple[str, ...]) -> tuple[LatestTrade, ...]:
        return tuple(
            LatestTrade(
                ticker=ticker,
                price=Decimal(103),
                observed_at=datetime(2026, 7, 20, 14, 1, tzinfo=UTC),
                source="fixture",
            )
            for ticker in tickers
        )


class _RunnerExits:
    async def run_brackets(
        self, *, as_of: date, prices: Mapping[str, Decimal]
    ) -> tuple[ExitDecision, ...]:
        _ = (as_of, prices)
        return ()


class _BlockingDispatch:
    provider_calls: int
    critic_calls: int
    order_calls: int

    def __init__(self) -> None:
        self.provider_started = anyio.Event()
        self.provider_calls = 0
        self.critic_calls = 0
        self.order_calls = 0

    async def run(
        self,
        *,
        now: datetime,
        prices: Mapping[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        _ = (now, prices)
        assert lease is not None
        claimed = await lease.claim_item("NVDA", "aggressive")
        if not claimed:
            return 0
        await lease.mark_dispatched("NVDA", "aggressive")
        self.provider_calls += 1
        self.provider_started.set()
        await anyio.sleep_forever()
        self.critic_calls += 1
        self.order_calls += 1
        await lease.complete_item("NVDA", "aggressive")
        return 1


class _CompletingDispatch(_BlockingDispatch):
    @override
    async def run(
        self,
        *,
        now: datetime,
        prices: Mapping[str, Decimal],
        lease: WorkLease | None = None,
    ) -> int:
        _ = (now, prices)
        assert lease is not None
        assert await lease.claim_item("NVDA", "aggressive")
        await lease.mark_dispatched("NVDA", "aggressive")
        self.provider_calls += 1
        self.critic_calls += 1
        self.order_calls += 1
        await lease.complete_item("NVDA", "aggressive")
        return 1


@pytest.mark.anyio
async def test_runner_cancels_lost_owner_and_reclaimed_sweep_skips_dispatched_item(
    domain: PostgresDomainRepository,
) -> None:
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    first_now = sweep_at + timedelta(minutes=1)
    second_now = sweep_at + timedelta(minutes=32)
    current_clock = [first_now]
    heartbeat_due = anyio.Event()
    rejudge = _BlockingDispatch()
    config = WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True))
    shared = _RunnerDomain(domain)
    first = WatchRunner(
        config,
        domain=shared,
        quotes=_RunnerQuotes(),
        exits=_RunnerExits(),
        rejudge=rejudge,
        clock=lambda: current_clock[0],
        heartbeat_wait=heartbeat_due.wait,
    )
    second = WatchRunner(
        config,
        domain=shared,
        quotes=_RunnerQuotes(),
        exits=_RunnerExits(),
        rejudge=rejudge,
        clock=lambda: current_clock[0],
    )
    first_error: list[BaseException] = []
    first_stopped = anyio.Event()
    outcome = None

    async def run_first() -> None:
        try:
            _ = await first.tick(first_now)
        except RuntimeError as error:
            first_error.append(error)
        finally:
            first_stopped.set()

    async with anyio.create_task_group() as task_group:
        _ = task_group.start_soon(run_first)
        await rejudge.provider_started.wait()
        current_clock[0] = second_now
        outcome = await second.tick(second_now)
        heartbeat_due.set()
        await first_stopped.wait()

    assert outcome is not None
    assert outcome.rejudged == 0
    assert len(first_error) == 1
    assert isinstance(first_error[0], WatchSweepStateError)
    assert (rejudge.provider_calls, rejudge.critic_calls, rejudge.order_calls) == (1, 0, 0)
    async with domain.engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT status, attempt
                    FROM tb_watch_sweep_item
                    WHERE sweep_at=:sweep_at
                      AND ticker='NVDA'
                      AND persona='aggressive'
                    """
                ),
                {"sweep_at": sweep_at},
            )
        ).one()
    assert tuple(row) == ("dispatched", 1)


@pytest.mark.anyio
async def test_runner_completes_one_unclaimed_item_once(
    domain: PostgresDomainRepository,
) -> None:
    now = datetime(2026, 7, 20, 14, 1, tzinfo=UTC)
    rejudge = _CompletingDispatch()
    outcome = await WatchRunner(
        WatchConfig(enabled=True, rejudge=RejudgeConfig(enabled=True)),
        domain=_RunnerDomain(domain),
        quotes=_RunnerQuotes(),
        exits=_RunnerExits(),
        rejudge=rejudge,
        clock=lambda: now,
    ).tick(now)

    assert outcome.rejudged == 1
    assert (rejudge.provider_calls, rejudge.critic_calls, rejudge.order_calls) == (1, 1, 1)
    async with domain.engine.begin() as connection:
        status = cast(
            "str | None",
            await connection.scalar(
                text(
                    """
                SELECT status
                FROM tb_watch_sweep_item
                WHERE sweep_at='2026-07-20 14:00:00+00'
                  AND ticker='NVDA'
                  AND persona='aggressive'
                """
                )
            ),
        )
    assert status == "completed"


@pytest.mark.anyio
async def test_concurrent_claims_have_exactly_one_owner(
    domain: PostgresDomainRepository,
) -> None:
    # Given
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    outcomes: list[bool] = []

    async def claim() -> None:
        outcomes.append((await domain.reserve_watch_sweep(sweep_at, now=sweep_at)) is not None)

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
    owner_b = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=31))
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
    restarted = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(hours=1))

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
    retry = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=2))

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
        claimed = await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=31))
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


@pytest.mark.anyio
async def test_dispatched_item_is_not_reclaimed_by_next_generation(
    domain: PostgresDomainRepository,
) -> None:
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    assert await domain.reserve_watch_sweep(sweep_at, now=sweep_at) == 1
    assert await domain.claim_watch_sweep_item(
        sweep_at,
        attempt=1,
        ticker="NVDA",
        persona="aggressive",
        now=sweep_at,
    )
    assert await domain.dispatch_watch_sweep_item(
        sweep_at,
        attempt=1,
        ticker="NVDA",
        persona="aggressive",
        now=sweep_at,
    )

    assert await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=31)) == 2
    assert not await domain.claim_watch_sweep_item(
        sweep_at,
        attempt=2,
        ticker="NVDA",
        persona="aggressive",
        now=sweep_at + timedelta(minutes=31),
    )
    assert not await domain.complete_watch_sweep_item(
        sweep_at,
        attempt=1,
        ticker="NVDA",
        persona="aggressive",
        now=sweep_at + timedelta(minutes=31),
    )

    async with domain.engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT status, attempt
                    FROM tb_watch_sweep_item
                    WHERE sweep_at=:sweep_at
                      AND ticker='NVDA'
                      AND persona='aggressive'
                    """
                ),
                {"sweep_at": sweep_at},
            )
        ).one()
    assert tuple(row) == ("dispatched", 1)


@pytest.mark.anyio
async def test_pre_dispatch_release_allows_next_generation_claim(
    domain: PostgresDomainRepository,
) -> None:
    sweep_at = datetime(2026, 7, 20, 14, tzinfo=UTC)
    assert await domain.reserve_watch_sweep(sweep_at, now=sweep_at) == 1
    assert await domain.claim_watch_sweep_item(
        sweep_at,
        attempt=1,
        ticker="MSFT",
        persona="conservative",
        now=sweep_at,
    )
    await domain.release_watch_sweep_item(
        sweep_at,
        attempt=1,
        ticker="MSFT",
        persona="conservative",
    )
    assert await domain.reserve_watch_sweep(sweep_at, now=sweep_at + timedelta(minutes=31)) == 2

    assert await domain.claim_watch_sweep_item(
        sweep_at,
        attempt=2,
        ticker="MSFT",
        persona="conservative",
        now=sweep_at + timedelta(minutes=31),
    )
    assert await domain.dispatch_watch_sweep_item(
        sweep_at,
        attempt=2,
        ticker="MSFT",
        persona="conservative",
        now=sweep_at + timedelta(minutes=31),
    )
    assert await domain.complete_watch_sweep_item(
        sweep_at,
        attempt=2,
        ticker="MSFT",
        persona="conservative",
        now=sweep_at + timedelta(minutes=31),
    )
