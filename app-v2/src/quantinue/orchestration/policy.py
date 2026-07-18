"""Typed runtime policy loaded from the canonical pipeline YAML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, assert_never
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError
from typing_extensions import override

from quantinue.core.config import AppOrderExposureUsd, DataMode, Settings

UTC_ZONE = ZoneInfo("UTC")

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class ThresholdPolicy(BaseModel):
    """Decision thresholds consumed by the pipeline roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_confidence: float = Field(ge=0, le=1)
    strategist_buy_score: float = Field(ge=0, le=1)
    critic_approval_score: float = Field(ge=0, le=1)
    maximum_risk_score: float = Field(ge=0, le=1)


class ModelPolicy(BaseModel):
    """Config-owned default model identifiers for each LLM adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mock: str = Field(min_length=1)
    openai: str = Field(min_length=1)
    local: str = Field(min_length=1)


def default_model_policy() -> ModelPolicy:
    """Return compatibility defaults for direct legacy construction."""
    return ModelPolicy(mock="deterministic-mock-v1", openai="gpt-4o-mini", local="qwen2.5:7b")


class PeriodicRolePlan(BaseModel):
    """One config-owned periodic role cadence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval_minutes: int = Field(gt=0, le=10_080)


class SchedulePlan(BaseModel):
    """MVP periodic roles; triggered relay roles are intentionally absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role_04: PeriodicRolePlan
    role_05: PeriodicRolePlan
    role_06: PeriodicRolePlan
    role_07: PeriodicRolePlan

    def periods(self) -> tuple[tuple[Literal["04", "05", "06", "07"], timedelta], ...]:
        """Project the validated plan into scheduler-ready periods."""
        return (
            ("04", timedelta(minutes=self.role_04.interval_minutes)),
            ("05", timedelta(minutes=self.role_05.interval_minutes)),
            ("06", timedelta(minutes=self.role_06.interval_minutes)),
            ("07", timedelta(minutes=self.role_07.interval_minutes)),
        )


def default_schedule_plan() -> SchedulePlan:
    """Return the design-contract MVP cadence for direct policy construction."""
    return SchedulePlan(
        role_04=PeriodicRolePlan(interval_minutes=60),
        role_05=PeriodicRolePlan(interval_minutes=60),
        role_06=PeriodicRolePlan(interval_minutes=30),
        role_07=PeriodicRolePlan(interval_minutes=120),
    )


class PipelinePolicy(BaseModel):
    """Finite role execution and explicit failed-run resume policy."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    role_timeout_seconds: float = Field(gt=0, le=300)
    role_max_retries: int = Field(ge=0, le=5)
    retry_base_delay_seconds: float = Field(ge=0, le=30)
    resume_failed: bool = True
    data_mode: DataMode = DataMode.FIXTURE
    stop_loss_ratio: float = Field(default=0.15, gt=0, lt=1)
    take_profit_ratio: float = Field(default=0.20, gt=0, le=10)
    max_app_order_exposure_usd: AppOrderExposureUsd = Decimal("1000.00")
    daily_new_order_cap: int = Field(default=1, ge=1, le=100)
    thresholds: ThresholdPolicy
    models: ModelPolicy = Field(default_factory=default_model_policy)
    schedule: SchedulePlan = Field(default_factory=default_schedule_plan)

    def apply_model_defaults(self, settings: Settings) -> Settings:
        """Apply YAML model defaults while preserving explicit env/input overrides."""
        updates: dict[str, str] = {}
        if "mock_model" not in settings.model_fields_set:
            updates["mock_model"] = self.models.mock
        if "openai_model" not in settings.model_fields_set:
            updates["openai_model"] = self.models.openai
        if "local_llm_model" not in settings.model_fields_set:
            updates["local_llm_model"] = self.models.local
        return settings.model_copy(update=updates)


DEFAULT_PIPELINE_POLICY = PipelinePolicy(
    role_timeout_seconds=30,
    role_max_retries=0,
    retry_base_delay_seconds=0,
    resume_failed=False,
    stop_loss_ratio=0.15,
    take_profit_ratio=0.20,
    thresholds=ThresholdPolicy(
        minimum_confidence=0.6,
        strategist_buy_score=0.65,
        critic_approval_score=0.6,
        maximum_risk_score=0.7,
    ),
    models=ModelPolicy(mock="deterministic-mock-v1", openai="gpt-4o-mini", local="qwen2.5:7b"),
)


class PipelineDocument(BaseModel):
    """Relevant typed projection of config/pipeline.yaml."""

    model_config = ConfigDict(frozen=True, extra="ignore", arbitrary_types_allowed=True)

    version: int = Field(ge=1)
    timezone: ZoneInfo
    mvp: PipelinePolicy

    @field_validator("timezone", mode="before")
    @classmethod
    def parse_timezone(cls, value: str | ZoneInfo) -> ZoneInfo:
        """Parse one IANA timezone at the YAML boundary."""
        match value:
            case ZoneInfo():
                return value
            case str() as name:
                try:
                    return ZoneInfo(name)
                except ZoneInfoNotFoundError as error:
                    code = "unknown_timezone"
                    message = "timezone must be an IANA name"
                    raise PydanticCustomError(code, message) from error
            case unreachable:
                assert_never(unreachable)

    def due_role_scheduler(self) -> DueRoleScheduler:
        """Build the pure scheduler with this document's timezone contract."""
        return DueRoleScheduler(self.mvp.schedule, self.timezone)


def load_pipeline_policy(path: Path) -> PipelinePolicy:
    """Parse trusted YAML syntax, then validate its runtime projection."""
    return load_pipeline_document(path).mvp


def load_pipeline_document(path: Path) -> PipelineDocument:
    """Parse the complete typed pipeline configuration document."""
    with path.open(encoding="utf-8") as stream:
        encoded = json.dumps(yaml.safe_load(stream))
    return PipelineDocument.model_validate_json(encoded)


@dataclass(frozen=True, slots=True)
class ScheduleTimeError(ValueError):
    """A scheduler command received a timezone-naive instant."""

    field_name: str

    @override
    def __str__(self) -> str:
        """Render the invalid scheduler field."""
        return f"{self.field_name} must be timezone-aware"


class DueRoleScheduler:
    """Pure command seam that reports due periodic roles without running a daemon."""

    def __init__(self, plan: SchedulePlan, timezone: ZoneInfo = UTC_ZONE) -> None:
        """Bind one immutable schedule plan."""
        self._plan = plan
        self._timezone = timezone

    def due_roles(
        self,
        at: datetime,
        last_runs: Mapping[str, datetime],
    ) -> tuple[str, ...]:
        """Return configured roles whose elapsed period has reached its cadence."""
        if at.tzinfo is None:
            field_name = "at"
            raise ScheduleTimeError(field_name)
        normalized_at = at.astimezone(self._timezone)
        due: list[str] = []
        for role, period in self._plan.periods():
            previous = last_runs.get(role)
            if previous is not None and previous.tzinfo is None:
                field_name = f"last_runs[{role}]"
                raise ScheduleTimeError(field_name)
            if previous is None or normalized_at - previous.astimezone(self._timezone) >= period:
                due.append(role)
        return tuple(due)
