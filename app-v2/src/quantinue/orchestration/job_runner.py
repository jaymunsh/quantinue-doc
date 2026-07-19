"""The background job runner: one tick decides, reserves, and runs each job.

재설계 §2의 "잡 간 핸드오프는 DB"를 실행하는 자리다. 11단계 선형 런과 달리
잡들은 서로를 기다리지 않는다 — 수집이 실패한 날에도 청산은 돌아야 하고,
분석이 죽은 날에도 일봉은 쌓여야 한다. 그래서 한 잡의 예외가 다음 잡을
건드리지 않게 잡마다 격리해서 잡는다.

``CycleScheduler``(11단계 사이클 트리거)와 나란히 서는 두 번째 루프다. 둘을
합치지 않은 이유: 사이클은 분 단위 슬롯·세션 인식이 필요하고 잡은 하루 단위
경과일이 기준이라, 같은 틱에 묶으면 둘 중 하나가 상대의 주기에 끌려간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import anyio
import structlog

from quantinue.core.market_calendar import NEW_YORK, NyseCalendar
from quantinue.orchestration.job_cadence import is_job_due

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from quantinue.orchestration.policy import JobsConfig


class JobRunLedger(Protocol):
    """The durable record that makes "once per day" survive a restart."""

    async def reserve_job_run(self, job_name: str, slot_date: date) -> bool:
        """Claim one job's slot, returning whether this caller won it."""
        ...

    async def finish_job_run(
        self,
        job_name: str,
        slot_date: date,
        *,
        succeeded: bool,
        detail: str | None = None,
    ) -> None:
        """Close out a reserved slot with its outcome."""
        ...

    async def last_job_success(self, job_name: str) -> date | None:
        """Return the last slot this job actually completed, if any."""
        ...


@dataclass(frozen=True, slots=True)
class JobDefinition:
    """One registered job: a name for the ledger and a body to run."""

    name: str
    run: Callable[[date], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """One job's verdict for this tick, kept observable for logs and the admin API."""

    name: str
    reason: str
    detail: str | None = None


class JobRunner:
    """Run every registered job at most once per trading day."""

    def __init__(
        self,
        config: JobsConfig,
        ledger: JobRunLedger,
        jobs: tuple[JobDefinition, ...],
        calendar: NyseCalendar | None = None,
    ) -> None:
        """Bind collaborators; each job owns its own side effects."""
        self._config = config
        self._ledger = ledger
        self._jobs = jobs
        self._calendar = calendar or NyseCalendar()
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("jobs")

    @property
    def jobs(self) -> tuple[JobDefinition, ...]:
        """Registered jobs in execution order — the order is a data dependency."""
        return self._jobs

    async def tick(self, now: datetime) -> tuple[JobOutcome, ...]:
        """Decide and run whatever is due, returning one outcome per job."""
        if not self._config.enabled:
            return tuple(JobOutcome(job.name, "disabled") for job in self._jobs)
        # 슬롯은 뉴욕 세션일이다. UTC 날짜를 쓰면 장중 20:00(뉴욕)에 날짜가
        # 넘어가 같은 세션에 잡이 두 번 돈다.
        as_of = now.astimezone(NEW_YORK).date()
        if not self._calendar.is_trading_day(as_of):
            # 세션이 없으면 새 일봉도 새 청산 대상도 없다(D4 정규장 전용).
            return tuple(JobOutcome(job.name, "holiday") for job in self._jobs)
        return tuple([await self._run_one(job, as_of) for job in self._jobs])

    async def _run_one(self, job: JobDefinition, as_of: date) -> JobOutcome:
        """Gate, reserve, and execute a single job without touching the others."""
        cadence = self._config.cadence_for(job.name)
        if not cadence.enabled:
            return JobOutcome(job.name, "job_disabled")
        last_success = await self._ledger.last_job_success(job.name)
        if not is_job_due(
            last_success=last_success, as_of=as_of, interval_days=cadence.interval_days
        ):
            return JobOutcome(job.name, "not_due")
        if not await self._ledger.reserve_job_run(job.name, as_of):
            # 다른 프로세스(또는 같은 날의 앞선 틱)가 이미 가져갔다.
            return JobOutcome(job.name, "already_reserved")
        try:
            detail = await job.run(as_of)
        except Exception as error:  # noqa: BLE001 — 한 잡의 실패가 나머지를 끊지 않게
            await self._ledger.finish_job_run(
                job.name, as_of, succeeded=False, detail=str(error)
            )
            await self._logger.aexception("jobs.failed", job=job.name)
            return JobOutcome(job.name, "failed", str(error))
        await self._ledger.finish_job_run(
            job.name, as_of, succeeded=True, detail=detail
        )
        return JobOutcome(job.name, "ran", detail)

    async def run_forever(self) -> None:
        """Tick forever; a failing tick is logged and never kills the loop."""
        while True:
            try:
                for outcome in await self.tick(datetime.now(UTC)):
                    if outcome.reason in {"ran", "failed"}:
                        await self._logger.ainfo(
                            "jobs.tick", job=outcome.name, reason=outcome.reason
                        )
            except Exception:  # noqa: BLE001 — 루프 생존이 우선
                await self._logger.aexception("jobs.tick.failed")
            await anyio.sleep(self._config.tick_seconds)
