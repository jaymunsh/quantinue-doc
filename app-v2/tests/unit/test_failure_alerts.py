"""실패 알림 — 키가 없으면 경로가 없고, 알림이 실패해도 매매는 계속된다."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from quantinue.core.config import Settings
from quantinue.notify.telegram import build_failure_notifier
from quantinue.orchestration.job_factory import build_daily_summary_job
from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.orchestration.policy import JobCadenceConfig, JobsConfig

_DAY = date(2026, 7, 20)


class IsolatedSettings(Settings):
    """개발자의 실제 .env를 안 읽는다 — 안 그러면 이 테스트가 그 파일에 달린다."""

    model_config = SettingsConfigDict(env_file=None, env_prefix="QUANTINUE_", extra="ignore")


class _Ledger:
    def __init__(self) -> None:
        self.finished: list[tuple[str, bool]] = []

    async def reserve_job_run(self, job_name: str, slot_date: date) -> bool:
        assert slot_date == _DAY
        assert job_name
        return True

    async def finish_job_run(
        self, job_name: str, slot_date: date, *, succeeded: bool, detail: str | None = None
    ) -> None:
        assert slot_date == _DAY
        assert detail is not None or succeeded
        self.finished.append((job_name, succeeded))

    async def last_job_success(self, job_name: str) -> date | None:
        assert job_name
        return None


def _config() -> JobsConfig:
    return JobsConfig(enabled=True, cadences={"boom": JobCadenceConfig(interval_days=1)})


async def _explode(as_of: date) -> str:
    msg = f"수집 실패 {as_of}"
    raise RuntimeError(msg)


def test_no_telegram_keys_means_no_alert_path() -> None:
    """빈 토큰으로 매번 401을 받는 것은 유령이다 — 경로 자체를 안 만든다."""
    # Given / When
    notifier = build_failure_notifier(IsolatedSettings(app_name="t"))

    # Then
    assert notifier is None


def test_a_half_configured_installation_still_has_no_path() -> None:
    """토큰만 있고 챗 ID가 없으면 보낼 곳이 없다."""
    # Given / When
    notifier = build_failure_notifier(
        IsolatedSettings(app_name="t", telegram_bot_token=SecretStr("x"))
    )

    # Then
    assert notifier is None


@pytest.mark.anyio
async def test_a_failed_job_announces_itself() -> None:
    # Given
    sent: list[str] = []

    async def notify(message: str) -> None:
        sent.append(message)

    ledger = _Ledger()
    runner = JobRunner(
        _config(), ledger, (JobDefinition("boom", _explode),), notifier=notify
    )

    # When
    outcomes = await runner.tick(datetime(2026, 7, 20, 14, tzinfo=UTC))

    # Then
    assert outcomes[0].reason == "failed"
    assert len(sent) == 1
    assert "boom" in sent[0]


@pytest.mark.anyio
async def test_a_broken_notifier_does_not_break_the_run() -> None:
    """텔레그램이 안 되는 것과 파이프라인이 안 도는 것은 다른 사건이다."""
    # Given
    async def notify(message: str) -> None:
        assert message
        raise ConnectionError

    ledger = _Ledger()
    runner = JobRunner(
        _config(), ledger, (JobDefinition("boom", _explode),), notifier=notify
    )

    # When / Then: 알림이 터져도 tick은 정상적으로 결과를 돌려준다
    with pytest.raises(ConnectionError):
        _ = await runner.tick(datetime(2026, 7, 20, 14, tzinfo=UTC))
    # 원장에는 실패가 이미 적혔다 — 알림보다 먼저 기록하기 때문이다
    assert ledger.finished == [("boom", False)]


class _SummaryReads:
    """오늘 슬롯의 잡 결과와 배분 판단만 답하는 최소 원장."""

    def __init__(self, runs: tuple[tuple[str, str], ...], planned: int) -> None:
        self._runs = runs
        self._planned = planned

    async def job_runs(self, slot_date: date) -> tuple[object, ...]:
        assert slot_date == _DAY
        return tuple(
            SimpleNamespace(job_name=name, status=status) for name, status in self._runs
        )

    async def order_plans(self, trade_date: date) -> tuple[object, ...]:
        assert trade_date == _DAY
        return tuple(SimpleNamespace(decision="planned") for _ in range(self._planned))


@pytest.mark.anyio
async def test_the_daily_note_reports_a_clean_chain() -> None:
    """안 오는 것이 신호가 되려면, 정상인 날에도 한 통은 와야 한다."""
    # Given
    sent: list[str] = []

    async def notify(message: str) -> None:
        sent.append(message)

    reads = _SummaryReads((("universe", "succeeded"), ("daily_bars", "succeeded")), 3)
    job = build_daily_summary_job(domain=reads, notify=notify)

    # When
    detail = await job.run(_DAY)

    # Then
    assert "✅" in sent[0]
    assert "2/2" in sent[0]
    assert "3건" in sent[0]
    assert "2/2" in detail


@pytest.mark.anyio
async def test_the_daily_note_names_what_broke() -> None:
    # Given
    sent: list[str] = []

    async def notify(message: str) -> None:
        sent.append(message)

    reads = _SummaryReads((("universe", "succeeded"), ("news", "failed")), 0)
    job = build_daily_summary_job(domain=reads, notify=notify)

    # When
    _ = await job.run(_DAY)

    # Then
    assert "⚠️" in sent[0]
    assert "실패: news" in sent[0]


@pytest.mark.anyio
async def test_the_daily_note_does_not_count_itself() -> None:
    """자기 자신은 지금 도는 중이라 결과가 없다 — 세면 늘 1개 실패로 보인다."""
    # Given
    sent: list[str] = []

    async def notify(message: str) -> None:
        sent.append(message)

    reads = _SummaryReads(
        (("universe", "succeeded"), ("daily_summary", "running")), 0
    )
    job = build_daily_summary_job(domain=reads, notify=notify)

    # When
    _ = await job.run(_DAY)

    # Then
    assert "1/1" in sent[0]
    assert "✅" in sent[0]
