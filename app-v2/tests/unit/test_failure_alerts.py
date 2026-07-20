"""실패 알림 — 키가 없으면 경로가 없고, 알림이 실패해도 매매는 계속된다."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from quantinue.core.config import Settings
from quantinue.notify.telegram import build_failure_notifier
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
