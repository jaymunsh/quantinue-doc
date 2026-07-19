"""The cadence rule that decides whether a background job runs today."""

from __future__ import annotations

from datetime import date

import pytest

from quantinue.orchestration.job_cadence import is_job_due


def test_never_run_job_is_due() -> None:
    assert is_job_due(last_success=None, as_of=date(2026, 7, 20), interval_days=7)


def test_job_that_already_succeeded_today_is_not_due() -> None:
    assert not is_job_due(
        last_success=date(2026, 7, 20), as_of=date(2026, 7, 20), interval_days=1
    )


def test_daily_job_is_due_the_next_day() -> None:
    assert is_job_due(
        last_success=date(2026, 7, 19), as_of=date(2026, 7, 20), interval_days=1
    )


def test_weekly_job_waits_the_full_interval() -> None:
    last = date(2026, 7, 13)
    assert not is_job_due(last_success=last, as_of=date(2026, 7, 19), interval_days=7)
    assert is_job_due(last_success=last, as_of=date(2026, 7, 20), interval_days=7)


def test_success_dated_after_today_does_not_retrigger() -> None:
    # 시계 역행·수동 백필로 미래 성공이 남을 수 있다. 그걸 "오래된 성공"으로
    # 읽어 다시 돌리면 하루에 두 번 도는 잡이 된다.
    assert not is_job_due(
        last_success=date(2026, 7, 21), as_of=date(2026, 7, 20), interval_days=1
    )


def test_missed_slot_runs_late_rather_than_skipping_the_period() -> None:
    # 주간 잡이 월요일을 놓쳤다면 다음 월요일까지 기다리는 게 아니라 화요일에 돈다.
    assert is_job_due(
        last_success=date(2026, 7, 6), as_of=date(2026, 7, 21), interval_days=7
    )


@pytest.mark.parametrize("interval", [0, -1])
def test_non_positive_interval_is_rejected(interval: int) -> None:
    with pytest.raises(ValueError, match="interval_days"):
        _ = is_job_due(
            last_success=None, as_of=date(2026, 7, 20), interval_days=interval
        )
