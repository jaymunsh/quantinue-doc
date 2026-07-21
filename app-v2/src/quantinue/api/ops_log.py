"""The operations log view: many days at a glance, from the job ledger alone.

관제실(`pipeline_day`)이 슬롯 하나를 깊게 본다면, 이 뷰는 여러 날을 한 화면에
펼친다 — "매일 돌았나, 몇 번 돌았나, 안내는 나갔나"가 질문이다. 숫자는 전부
``tb_job_run``이 답할 수 있는 것만 싣는다: 시도 횟수는 ``attempts`` 컬럼이,
안내 발송은 ``daily_summary`` 잡의 성공 행이 근거다.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - pydantic이 런타임에 필드 타입을 해석한다
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from quantinue.db.control_room_reads import JobRunRecord

# 한 화면에 펼치는 날 수. 표시용 창이라 config가 아니라 여기 산다 —
# 어떤 매매 결정에도 들어가지 않는다.
DEFAULT_LOG_DAYS = 14

# 일일 안내 잡의 원장 이름. 이 행의 성공이 곧 "그날 안내가 나갔다"다.
_SUMMARY_JOB = "daily_summary"


class OpsLogReads(Protocol):
    """The two ledger reads this page needs."""

    async def recent_job_slots(self, *, limit: int) -> tuple[date, ...]:
        """Return the days the runner touched, newest first."""
        ...

    async def job_runs(self, slot_date: date) -> tuple[JobRunRecord, ...]:
        """List one day's job chain in execution order."""
        ...


class OpsLogJobView(BaseModel):
    """One job's row in the log."""

    model_config = ConfigDict(frozen=True)

    job_name: str
    status: str
    attempts: int = Field(ge=1)
    detail: str | None
    started_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)


class OpsLogSlotView(BaseModel):
    """One day's verdict: did the chain run, how many times, was it announced."""

    model_config = ConfigDict(frozen=True)

    slot_date: date
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    running: int = Field(ge=0)
    # 재시도가 있었던 잡 수(attempts > 1). "하루에 여러 번 돌았다"의 원장 표현.
    retried_jobs: int = Field(ge=0)
    summary_sent: bool = False
    jobs: tuple[OpsLogJobView, ...] = ()


class OpsLogView(BaseModel):
    """The whole log page, newest day first."""

    model_config = ConfigDict(frozen=True)

    slots: tuple[OpsLogSlotView, ...] = ()


def _job_view(record: JobRunRecord) -> OpsLogJobView:
    duration_ms = None
    if record.finished_at is not None:
        duration_ms = max(
            0, int((record.finished_at - record.started_at).total_seconds() * 1000)
        )
    return OpsLogJobView(
        job_name=record.job_name,
        status=record.status,
        attempts=record.attempts,
        detail=record.detail,
        started_at=record.started_at,
        duration_ms=duration_ms,
    )


def _slot_view(slot: date, records: tuple[JobRunRecord, ...]) -> OpsLogSlotView:
    return OpsLogSlotView(
        slot_date=slot,
        total=len(records),
        succeeded=sum(1 for item in records if item.status == "succeeded"),
        failed=sum(1 for item in records if item.status == "failed"),
        running=sum(1 for item in records if item.status == "running"),
        retried_jobs=sum(1 for item in records if item.attempts > 1),
        summary_sent=any(
            item.job_name == _SUMMARY_JOB and item.status == "succeeded"
            for item in records
        ),
        jobs=tuple(_job_view(item) for item in records),
    )


async def build_ops_log(reads: OpsLogReads, *, days: int = DEFAULT_LOG_DAYS) -> OpsLogView:
    """Project the recent slots into the log, newest first."""
    slots = await reads.recent_job_slots(limit=days)
    return OpsLogView(
        slots=tuple([_slot_view(slot, await reads.job_runs(slot)) for slot in slots])
    )
