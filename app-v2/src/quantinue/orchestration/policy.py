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


class Mvp2ScheduleConfig(BaseModel):
    """Automatic cycle trigger cadence and gates; disabled until armed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    tick_seconds: int = Field(default=60, gt=0, le=3_600)
    cycle_slot_minutes: int = Field(default=30, gt=0, le=1_440)
    trigger_sessions: tuple[Literal["pre", "regular", "after"], ...] = (
        "pre",
        "regular",
        "after",
    )


class ProfileConfig(BaseModel):
    """Per-investment-type thresholds, sizing limits, and circuit breakers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    buy_threshold: float = Field(default=0.65, ge=0, le=1)
    risk_off_action: Literal["penalty", "no_new_buys"] = "penalty"
    late_entry_max: float = Field(default=0.15, ge=0, le=1)
    max_positions: int = Field(default=10, gt=0, le=100)
    max_weight: float = Field(default=0.20, gt=0, le=1)
    daily_loss_limit: float = Field(default=0.04, gt=0, le=1)
    min_cash_ratio: float = Field(default=0.10, ge=0, le=1)


class GatesConfig(BaseModel):
    """Decision-defence thresholds applied by roles 07 and 08."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_trust_min: float = Field(default=0.55, ge=0, le=1)
    hard_negative_max: float = Field(default=0.15, ge=0, le=1)
    macro_penalty_cap: float = Field(default=0.40, ge=0, le=1)
    snapshot_tolerance: float = Field(default=0.02, ge=0, le=1)
    critic_approval: float = Field(default=0.70, ge=0, le=1)
    overconfidence_conviction: float = Field(default=0.90, ge=0, le=1)
    overconfidence_approval: float = Field(default=0.80, ge=0, le=1)


class ScreeningConfig(BaseModel):
    """Funnel widths from the raw universe down to LLM-depth candidates.

    `technical_candidates` exists because daily candles are fetched one ticker
    per request (~3s each on the public NASDAQ endpoint). The full universe is
    still stored, but only the largest-cap slice is priced for indicators, so a
    cycle finishes inside the premarket window without hammering the source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_size: int = Field(default=2000, gt=0, le=10_000)
    min_price_usd: float = Field(default=5, ge=0)
    min_avg_dollar_vol: float = Field(default=20_000_000, ge=0)
    technical_candidates: int = Field(default=500, gt=0, le=10_000)
    technical_concurrency: int = Field(default=10, gt=0, le=64)
    dollar_volume_window: int = Field(default=20, gt=0, le=250)
    daily_picks: int = Field(default=50, gt=0, le=500)
    llm_depth: int = Field(default=20, gt=0, le=200)


class ExitsConfig(BaseModel):
    """Holding-period exit policy measured in trading sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    time_exit_bdays: int = Field(default=10, gt=0, le=250)


class BudgetConfig(BaseModel):
    """Spending ceiling enforced before any billable model call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_llm_usd: float = Field(default=3.0, ge=0)


def _default_profiles() -> dict[str, ProfileConfig]:
    return {
        "aggressive": ProfileConfig(),
        "conservative": ProfileConfig(
            buy_threshold=0.75,
            risk_off_action="no_new_buys",
            late_entry_max=0.12,
            max_positions=5,
            max_weight=0.10,
            daily_loss_limit=0.02,
            min_cash_ratio=0.30,
        ),
    }


class Mvp2Config(BaseModel):
    """MVP-2 configuration surface owned by config/pipeline.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule: Mvp2ScheduleConfig = Mvp2ScheduleConfig()
    profiles: dict[str, ProfileConfig] = Field(default_factory=_default_profiles)
    gates: GatesConfig = GatesConfig()
    screening: ScreeningConfig = ScreeningConfig()
    exits: ExitsConfig = ExitsConfig()
    budget: BudgetConfig = BudgetConfig()


def load_mvp2_config(path: Path) -> Mvp2Config:
    """Load the mvp2 block; an absent block yields safe defaults (disabled)."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    return Mvp2Config.model_validate(document.get("mvp2") or {})


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

    def plan_periods(self) -> tuple[tuple[str, timedelta], ...]:
        """Expose the bound plan's role cadences for cycle-level consumers."""
        return tuple(self._plan.periods())

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
