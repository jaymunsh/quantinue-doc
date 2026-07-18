from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantinue.broker.provider import MockBroker
from quantinue.core.config import DataMode, LlmMode, Settings
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.policy import (
    DueRoleScheduler,
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
