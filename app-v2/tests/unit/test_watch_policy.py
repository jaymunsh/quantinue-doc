import pytest
from pydantic import ValidationError

from quantinue.orchestration.policy import Mvp2Config, RejudgeConfig, WatchConfig


def test_watch_config_defaults_to_a_disabled_one_minute_regular_session() -> None:
    # Given / When
    config = WatchConfig()

    # Then
    assert config.enabled is False
    assert config.interval_minutes == 1
    assert config.session == "regular"
    assert config.rejudge == RejudgeConfig()


def test_rejudge_config_owns_the_confirmed_intraday_limits() -> None:
    # Given / When
    config = RejudgeConfig()

    # Then
    assert config.enabled is False
    assert config.move_trigger_pct == 0.05
    assert config.cooldown_minutes == 30
    assert config.sweep_times_ny == ("10:00", "12:45", "15:15")
    assert config.sell_budget_reserve_ratio == 0.20


def test_mvp2_config_owns_the_watch_config() -> None:
    # Given / When
    config = Mvp2Config.model_validate(
        {"watch": {"enabled": True, "interval_minutes": 5, "session": "regular"}}
    )

    # Then
    assert config.watch == WatchConfig(enabled=True, interval_minutes=5)


def test_watch_config_rejects_an_unknown_session() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        WatchConfig.model_validate({"session": "extended"})
