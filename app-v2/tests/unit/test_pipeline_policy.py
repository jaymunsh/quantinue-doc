from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantinue.broker.provider import MockBroker
from quantinue.core.config import DataMode, LlmMode, Settings
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.policy import (
    DueRoleScheduler,
    Mvp2ScheduleConfig,
    ProfileConfig,
    load_mvp2_config,
    load_pipeline_document,
    load_pipeline_policy,
)
from quantinue.roles.role_07_strategist.service import Strategist
from quantinue.roles.role_08_critic.service import Critic
from quantinue.roles.role_09_risk_portfolio.service import RiskPortfolio


def test_pipeline_yaml_drives_runtime_resilience_policy() -> None:
    policy = load_pipeline_policy(Path("config/pipeline.yaml"))

    assert policy.role_timeout_seconds == 120
    assert policy.role_max_retries == 2
    assert policy.retry_base_delay_seconds == 0.25
    assert policy.thresholds.minimum_confidence == 0.60
    assert policy.resume_failed is True
    assert policy.data_mode is DataMode.FIXTURE
    assert policy.stop_loss_ratio == 0.15
    assert policy.take_profit_ratio == 0.20


def test_pipeline_yaml_owns_model_defaults_unless_environment_overrides() -> None:
    # Given
    policy = load_pipeline_policy(Path("config/pipeline.yaml"))

    # When
    defaults = policy.apply_model_defaults(Settings())
    override = policy.apply_model_defaults(
        Settings.model_validate(
            {"llm_mode": LlmMode.OPENAI, "openai_api_key": "key", "openai_model": "env-model"}
        )
    )

    # Then
    assert defaults.openai_model == "gpt-4o-mini"
    assert defaults.local_llm_model == "qwen2.5:7b"
    assert override.openai_model == "env-model"


def test_yaml_decision_policy_is_injected_into_roles_07_through_09() -> None:
    # Given
    policy = load_pipeline_policy(Path("config/pipeline.yaml"))

    # When
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), policy=policy)

    # Then
    strategist = roles[6]
    critic = roles[7]
    risk = roles[8]
    assert isinstance(strategist, Strategist)
    assert strategist.strategist_buy_score == 0.65
    assert isinstance(critic, Critic)
    assert critic.critic_approval_score == 0.60
    assert isinstance(risk, RiskPortfolio)
    assert risk.maximum_risk_score == 0.70
    assert risk.stop_loss_ratio == 0.15
    assert risk.take_profit_ratio == 0.20


def test_pipeline_yaml_drives_typed_schedule_plan() -> None:
    # Given / When
    document = load_pipeline_document(Path("config/pipeline.yaml"))

    # Then
    assert document.mvp.schedule.role_04.interval_minutes == 60
    assert document.mvp.schedule.role_06.interval_minutes == 30
    assert document.mvp.schedule.role_07.interval_minutes == 120
    assert document.timezone.key == "America/New_York"


def test_due_role_scheduler_returns_only_elapsed_periodic_roles() -> None:
    # Given
    now = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)
    document = load_pipeline_document(Path("config/pipeline.yaml"))
    scheduler = DueRoleScheduler(document.mvp.schedule, document.timezone)
    last_runs = {
        "04": now - timedelta(minutes=61),
        "05": now - timedelta(minutes=10),
        "06": now - timedelta(minutes=31),
        "07": now - timedelta(minutes=119),
    }

    # When
    due = scheduler.due_roles(now, last_runs)

    # Then
    assert due == ("04", "06")


def test_due_role_scheduler_rejects_naive_schedule_times() -> None:
    # Given
    scheduler = load_pipeline_document(Path("config/pipeline.yaml")).due_role_scheduler()

    # When / Then
    with pytest.raises(ValueError, match="timezone-aware"):
        _ = scheduler.due_roles(
            datetime(2026, 7, 13, 20, 0),  # noqa: DTZ001 - intentional naive boundary input
            {},
        )


def test_mvp2_schedule_config_loads_from_yaml() -> None:
    config = load_mvp2_config(Path("config/pipeline.yaml"))

    assert config.schedule.enabled is False  # 기본은 꺼짐 — W0 수동 운용 보호
    assert config.schedule.tick_seconds == 60
    assert config.schedule.cycle_slot_minutes == 30
    assert config.schedule.trigger_sessions == ("pre", "regular", "after")


def test_mvp2_schedule_rejects_unknown_session() -> None:
    with pytest.raises(ValidationError):
        Mvp2ScheduleConfig(trigger_sessions=("lunch",))  # type: ignore[arg-type]


def test_mvp2_profiles_and_gates_load_from_yaml() -> None:
    config = load_mvp2_config(Path("config/pipeline.yaml"))

    aggressive = config.profiles["aggressive"]
    conservative = config.profiles["conservative"]
    assert aggressive.buy_threshold == 0.65
    assert aggressive.max_positions == 10
    assert aggressive.daily_loss_limit == 0.04
    assert aggressive.risk_off_action == "penalty"
    assert conservative.buy_threshold == 0.75
    assert conservative.min_cash_ratio == 0.30
    assert conservative.risk_off_action == "no_new_buys"
    assert config.gates.source_trust_min == 0.55
    assert config.gates.macro_penalty_cap == 0.40
    assert config.gates.snapshot_tolerance == 0.02
    assert config.gates.overconfidence_approval == 0.80
    assert config.screening.universe_size == 2000
    assert config.screening.daily_picks == 50
    assert config.screening.llm_depth == 20
    assert config.exits.time_exit_bdays == 10
    assert config.budget.daily_llm_usd == 3.0  # 임시값 — M8 실측 후 확정


def test_mvp2_profile_rejects_out_of_range_threshold() -> None:
    with pytest.raises(ValidationError):
        ProfileConfig(buy_threshold=1.5)


def test_mvp2_screening_caps_technical_fetch_work() -> None:
    config = load_mvp2_config(Path("config/pipeline.yaml"))

    # 유니버스는 전부 저장하되, 일봉 조회(종목당 ~3s)는 상위 N개로 제한한다.
    assert config.screening.universe_size == 2000
    assert config.screening.technical_candidates == 500
    assert config.screening.technical_concurrency == 10
    assert config.screening.dollar_volume_window == 20
    assert config.screening.technical_candidates <= config.screening.universe_size
    assert config.screening.llm_depth <= config.screening.daily_picks


def test_role_timeout_override_gives_screening_a_longer_deadline() -> None:
    policy = load_pipeline_policy(Path("config/pipeline.yaml"))

    # 스크리닝(02)은 수백 종목 일봉을 받아야 해 기본 한도로는 못 끝낸다.
    assert policy.timeout_for("02") > policy.role_timeout_seconds
    # 나머지 역할은 기본 보호를 그대로 유지한다.
    assert policy.timeout_for("07") == policy.role_timeout_seconds
    assert policy.timeout_for("10") == policy.role_timeout_seconds


def test_macro_penalty_table_matches_frozen_first_generation_policy() -> None:
    gates = load_mvp2_config(Path("config/pipeline.yaml")).gates

    # 1차 동결본 s07 POLICY 이관: 0.5→-0.05 · 0.6→-0.10 · 0.7→-0.15
    #                              0.8→-0.20 · 0.9→-0.30 · 1.0→-0.40
    assert gates.macro_penalty(0.0) == 0.0
    assert gates.macro_penalty(0.49) == 0.0
    assert gates.macro_penalty(0.50) == 0.05
    assert gates.macro_penalty(0.59) == 0.05
    assert gates.macro_penalty(0.60) == 0.10
    assert gates.macro_penalty(0.70) == 0.15
    assert gates.macro_penalty(0.80) == 0.20
    assert gates.macro_penalty(0.90) == 0.30
    assert gates.macro_penalty(1.00) == 0.40


def test_macro_penalty_never_exceeds_the_configured_cap() -> None:
    gates = load_mvp2_config(Path("config/pipeline.yaml")).gates

    assert gates.macro_penalty(1.0) <= gates.macro_penalty_cap
