"""The job runner: cadence gate → slot reservation → job body → outcome."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.orchestration.policy import JobCadenceConfig, JobsConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

# 2026-07-20 월요일 09:00 뉴욕 = 정규 거래일
_MONDAY = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)
_SATURDAY = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)


class _Ledger:
    """In-memory stand-in for tb_job_run with the same reservation semantics."""

    def __init__(self, successes: dict[str, date] | None = None) -> None:
        self.successes = dict(successes or {})
        self.reserved: set[tuple[str, date]] = set()
        self.failed: set[tuple[str, date]] = set()
        self.finished: list[tuple[str, date, bool, str | None]] = []

    async def reserve_job_run(self, job_name: str, slot_date: date) -> bool:
        key = (job_name, slot_date)
        if key in self.reserved and key not in self.failed:
            return False
        self.reserved.add(key)
        self.failed.discard(key)
        return True

    async def finish_job_run(
        self,
        job_name: str,
        slot_date: date,
        *,
        succeeded: bool,
        detail: str | None = None,
    ) -> None:
        self.finished.append((job_name, slot_date, succeeded, detail))
        if succeeded:
            self.successes[job_name] = slot_date
        else:
            self.failed.add((job_name, slot_date))

    async def last_job_success(self, job_name: str) -> date | None:
        return self.successes.get(job_name)


def _runner(
    jobs: Sequence[JobDefinition],
    ledger: _Ledger,
    *,
    enabled: bool = True,
    cadences: dict[str, JobCadenceConfig] | None = None,
) -> JobRunner:
    return JobRunner(
        config=JobsConfig(enabled=enabled, cadences=cadences or {}),
        ledger=ledger,
        jobs=tuple(jobs),
    )


def _recorder(name: str, calls: list[date]) -> JobDefinition:
    async def run(as_of: date) -> str:
        calls.append(as_of)
        return f"{name} ok"

    return JobDefinition(name=name, run=run)


@pytest.mark.anyio
async def test_a_due_job_runs_and_is_recorded_as_succeeded() -> None:
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    runner = _runner([_recorder("collect", calls)], ledger)

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert calls == [date(2026, 7, 20)]
    assert [(o.name, o.reason) for o in outcomes] == [("collect", "ran")]
    assert ledger.finished == [("collect", date(2026, 7, 20), True, "collect ok")]


@pytest.mark.anyio
async def test_a_second_tick_the_same_day_does_not_rerun_the_job() -> None:
    """스케줄러는 60초마다 깨어난다 — 잡 본문은 그중 한 번만 돌아야 한다."""
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    runner = _runner([_recorder("collect", calls)], ledger)
    _ = await runner.tick(_MONDAY)

    # When
    outcomes = await runner.tick(_MONDAY.replace(hour=14))

    # Then
    assert calls == [date(2026, 7, 20)]
    assert [o.reason for o in outcomes] == ["not_due"]


@pytest.mark.anyio
async def test_a_weekly_job_is_skipped_until_its_interval_elapses() -> None:
    # Given
    calls: list[date] = []
    ledger = _Ledger({"universe": date(2026, 7, 16)})
    runner = _runner(
        [_recorder("universe", calls)],
        ledger,
        cadences={"universe": JobCadenceConfig(interval_days=7)},
    )

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert calls == []
    assert [o.reason for o in outcomes] == ["not_due"]


@pytest.mark.anyio
async def test_jobs_do_not_run_on_a_non_trading_day() -> None:
    """일봉도 청산도 세션이 없는 날엔 할 일이 없다(D4 정규장 전용)."""
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    runner = _runner([_recorder("collect", calls)], ledger)

    # When
    outcomes = await runner.tick(_SATURDAY)

    # Then
    assert calls == []
    assert [o.reason for o in outcomes] == ["holiday"]


@pytest.mark.anyio
async def test_the_whole_runner_can_be_switched_off() -> None:
    # Given
    calls: list[date] = []
    runner = _runner([_recorder("collect", calls)], _Ledger(), enabled=False)

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert calls == []
    assert [o.reason for o in outcomes] == ["disabled"]


@pytest.mark.anyio
async def test_one_job_can_be_switched_off_without_touching_the_others() -> None:
    # Given
    calls: list[date] = []
    runner = _runner(
        [_recorder("collect", calls), _recorder("exits", calls)],
        _Ledger(),
        cadences={"collect": JobCadenceConfig(enabled=False)},
    )

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert [(o.name, o.reason) for o in outcomes] == [
        ("collect", "job_disabled"),
        ("exits", "ran"),
    ]


@pytest.mark.anyio
async def test_a_failing_job_is_recorded_as_failed_and_not_counted_as_success() -> None:
    """실패를 성공으로 세면 다음 주기까지 재시도가 막힌다."""
    # Given
    ledger = _Ledger()

    async def explode(as_of: date) -> str:
        msg = "upstream down"
        raise RuntimeError(msg)

    runner = _runner([JobDefinition(name="collect", run=explode)], ledger)

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert [o.reason for o in outcomes] == ["failed"]
    assert ledger.finished[0][2] is False
    assert "upstream down" in str(ledger.finished[0][3])
    assert await ledger.last_job_success("collect") is None


@pytest.mark.anyio
async def test_one_failing_job_does_not_stop_the_next_one() -> None:
    """청산이 분석 실패에 끌려 내려가면 안 된다 — 잡을 나눈 이유 그 자체다."""
    # Given
    calls: list[date] = []

    async def explode(as_of: date) -> str:
        msg = "boom"
        raise RuntimeError(msg)

    runner = _runner(
        [JobDefinition(name="collect", run=explode), _recorder("exits", calls)],
        _Ledger(),
    )

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert [(o.name, o.reason) for o in outcomes] == [
        ("collect", "failed"),
        ("exits", "ran"),
    ]
    assert calls == [date(2026, 7, 20)]


@pytest.mark.anyio
async def test_losing_the_reservation_race_skips_the_body() -> None:
    """프로세스가 둘이어도 잡 본문은 한 번만 돈다 — 판정은 DB에 있다."""
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    _ = await ledger.reserve_job_run("collect", date(2026, 7, 20))
    runner = _runner([_recorder("collect", calls)], ledger)

    # When
    outcomes = await runner.tick(_MONDAY)

    # Then
    assert calls == []
    assert [o.reason for o in outcomes] == ["already_reserved"]


@pytest.mark.anyio
async def test_the_slot_date_is_the_new_york_session_not_the_utc_date() -> None:
    """뉴욕 장중 21:00은 UTC로 이미 다음 날이다 — 그날 잡이 두 번 돌면 안 된다."""
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    runner = _runner([_recorder("collect", calls)], ledger)

    # When: 2026-07-21 01:00 UTC = 2026-07-20 21:00 뉴욕 (UTC 날짜는 이미 넘어갔다)
    _ = await runner.tick(datetime(2026, 7, 21, 1, 0, tzinfo=UTC))

    # Then
    assert calls == [date(2026, 7, 20)]


@pytest.mark.anyio
async def test_a_failed_job_retries_on_the_next_tick_not_tomorrow() -> None:
    """수집이 한 번 실패했다고 하루를 묵은 봉으로 보내면 안 된다."""
    # Given: 첫 틱은 실패, 두 번째 틱은 성공하는 잡
    attempts: list[date] = []

    async def flaky(as_of: date) -> str:
        attempts.append(as_of)
        if len(attempts) == 1:
            msg = "transient"
            raise RuntimeError(msg)
        return "recovered"

    ledger = _Ledger()
    runner = _runner([JobDefinition(name="collect", run=flaky)], ledger)
    first = await runner.tick(_MONDAY)

    # When: 같은 날 다음 틱
    second = await runner.tick(_MONDAY.replace(hour=14))

    # Then
    assert [o.reason for o in first] == ["failed"]
    assert [o.reason for o in second] == ["ran"]
    assert attempts == [date(2026, 7, 20), date(2026, 7, 20)]
    assert await ledger.last_job_success("collect") == date(2026, 7, 20)


@pytest.mark.anyio
async def test_a_succeeded_job_is_not_reclaimed_by_a_later_tick() -> None:
    """재시도를 여는 것이 성공까지 다시 열어서는 안 된다."""
    # Given
    calls: list[date] = []
    ledger = _Ledger()
    runner = _runner([_recorder("collect", calls)], ledger)
    _ = await runner.tick(_MONDAY)

    # When
    outcomes = await runner.tick(_MONDAY.replace(hour=15))

    # Then: 주기 게이트에서 먼저 걸리고, 예약까지 가지도 않는다
    assert [o.reason for o in outcomes] == ["not_due"]
    assert calls == [date(2026, 7, 20)]
