"""Typed runtime policy loaded from the canonical pipeline YAML."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal, assert_never
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
    role_timeout_overrides: dict[str, float] = Field(default_factory=dict)

    def timeout_for(self, component: str) -> float:
        """Return the deadline for one role.

        Screening pulls daily candles one ticker per request, so it needs a
        far longer deadline than a single model call. Overriding per role keeps
        the tight default guarding everything else.
        """
        return self.role_timeout_overrides.get(component, self.role_timeout_seconds)

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
    # 매도 문턱(약세 확신 기준). 매수보다 낮은 것이 의도다 — 좋은 종목을 안 사면
    # 기회를 놓칠 뿐이지만, 나쁜 종목을 안 팔면 손실이 계속 자란다.
    sell_threshold: float = Field(default=0.60, ge=0, le=1)
    risk_off_action: Literal["penalty", "no_new_buys"] = "penalty"
    late_entry_max: float = Field(default=0.15, ge=0, le=1)
    max_positions: int = Field(default=10, gt=0, le=100)
    max_weight: float = Field(default=0.20, gt=0, le=1)
    daily_loss_limit: float = Field(default=0.04, gt=0, le=1)
    min_cash_ratio: float = Field(default=0.10, ge=0, le=1)


DEFAULT_MACRO_PENALTIES: Final[tuple[tuple[float, float], ...]] = (
    (0.50, 0.05),
    (0.60, 0.10),
    (0.70, 0.15),
    (0.80, 0.20),
    (0.90, 0.30),
    (1.00, 0.40),
)


class GatesConfig(BaseModel):
    """Decision-defence thresholds applied by roles 07 and 08."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_trust_min: float = Field(default=0.55, ge=0, le=1)
    # 증거가 이보다 오래되면 매수를 막는다. 같은 코드가 두 케이던스를 도므로
    # 코드 리터럴이면 안 된다 — 11단계 러너는 분 단위 증거를, 일 1회 잡은
    # 어제 닫힌 세션을 본다. 기본 5분은 구 경로의 동작을 그대로 보존한다.
    evidence_max_age_minutes: int = Field(default=5, gt=0, le=20_160)
    hard_negative_max: float = Field(default=0.15, ge=0, le=1)
    macro_penalty_cap: float = Field(default=0.40, ge=0, le=1)
    macro_penalty_table: tuple[tuple[float, float], ...] = DEFAULT_MACRO_PENALTIES
    snapshot_tolerance: float = Field(default=0.02, ge=0, le=1)
    critic_approval: float = Field(default=0.70, ge=0, le=1)
    overconfidence_conviction: float = Field(default=0.90, ge=0, le=1)
    overconfidence_approval: float = Field(default=0.80, ge=0, le=1)
    premarket_gap_max: float = Field(default=0.03, ge=0, le=1)
    gap_guard_open_minutes: int = Field(default=30, ge=0)

    def macro_penalty(self, risk_score: float) -> float:
        """Return the conviction deduction for one macro risk score.

        A hostile regime should shrink conviction rather than veto outright, so
        the table is a graded slope bounded by the cap.
        """
        penalty = 0.0
        for threshold, deduction in self.macro_penalty_table:
            if risk_score >= threshold:
                penalty = deduction
        return min(penalty, self.macro_penalty_cap)


class DisclosureConfig(BaseModel):
    """Which filing forms bypass the LLM because they carry no readable signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm_bypass_forms: tuple[str, ...] = ("4", "4/A", "3", "5")

    def is_bypassed(self, form: str) -> bool:
        """Return whether this filing form should never reach the model."""
        return form.strip().upper() in {item.upper() for item in self.llm_bypass_forms}


class NewsConfig(BaseModel):
    """How much news we collect, and how much of it a judgement gets to see."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 한 페이지에 받는 기사 수. Alpaca 뉴스의 상한이 50이고 넘기면 400이라
    # (실측) 이 값은 위로 열려 있지 않다 — 낮추는 쪽으로만 의미가 있다.
    page_size: int = Field(default=50, gt=0, le=50)
    # 종목당 프롬프트에 넣는 헤드라인 수. 예산이자 방어선이다 — 안 자르면
    # 시끄러운 종목 하나가 그날 판단의 컨텍스트를 통째로 차지한다.
    headlines_per_ticker: int = Field(default=5, gt=0, le=50)


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
    # 랭킹 후보가 되기 위한 최소 봉 개수. 신규 상장은 짧은 이력만으로 52주
    # 고가에 붙어 있어 돌파로 오인되고, ma50도 의미를 갖지 못한다.
    min_history_sessions: int = Field(default=60, gt=0, le=500)


class ExitsConfig(BaseModel):
    """Holding-period exit policy measured in trading sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    time_exit_bdays: int = Field(default=10, gt=0, le=250)


class AllocationConfig(BaseModel):
    """The allocation job's sizing brackets and hard ceilings.

    값은 구 러너의 mvp 블록(stop_loss_ratio 등)과 같다 — 소유권을 mvp2로
    옮기는 중이고, 구 러너가 죽으면 mvp 블록이 함께 죽는다. 두 블록이 다른
    값을 갖는 상태는 과도기에만 허용된다(핸드오프에 기록).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_loss_ratio: float = Field(default=0.15, gt=0, lt=1)
    take_profit_ratio: float = Field(default=0.20, gt=0, le=10)
    maximum_risk_score: float = Field(default=0.70, ge=0, le=1)
    # 하루에 새로 여는 포지션 수의 계좌당 상한. 기본 5는 redesign §7이 말한
    # 실효 의도값이다 — 코드 4곳의 리터럴(1·1·1·5)이 서로 달랐고, 이제 이
    # 키가 배분 잡의 단일 소유자다.
    daily_new_order_cap: int = Field(default=5, ge=1, le=100)


class MarketDataConfig(BaseModel):
    """Request shaping for the batch market-data adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 한 요청에 넣을 종목 수. Alpaca는 종목 수 상한을 문서화하지 않았고 분당
    # 호출 한도도 공식 문서에서 확인되지 않았다 — 추정해 박는 대신 URL 길이가
    # 안전한 값에서 시작하고, 실측 후 여기서 조인다.
    symbols_per_request: int = Field(default=200, gt=0, le=2_000)
    # 봉이 없는 종목을 처음 볼 때 소급해 받는 **달력일** 수(거래일이 아니다).
    # 스크리닝의 가장 긴 창이 52주라 그보다 넉넉해야 하고, 상한을 둔 이유는
    # 이 값이 곧 첫 실행의 응답 크기이기 때문이다.
    history_days: int = Field(default=400, gt=0, le=3_650)


class JobCadenceConfig(BaseModel):
    """How often one background job runs, measured in days."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    interval_days: int = Field(default=1, gt=0, le=365)


class JobsConfig(BaseModel):
    """The background job runner's switch and per-job cadences.

    주기를 잡 이름으로 찾는 사전으로 둔 이유: 잡이 늘 때마다 이 모델에 필드를
    추가하면 config가 코드 변경을 강제한다(D3이 막으려던 것). 선언이 없는
    잡은 기본값(일 1회)으로 돌고, 조이거나 끄고 싶을 때만 yaml에 적는다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    tick_seconds: int = Field(default=60, gt=0, le=3_600)
    cadences: dict[str, JobCadenceConfig] = Field(default_factory=dict)

    def cadence_for(self, job_name: str) -> JobCadenceConfig:
        """Return the declared cadence, or the default daily one."""
        return self.cadences.get(job_name, JobCadenceConfig())


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
    disclosure: DisclosureConfig = DisclosureConfig()
    news: NewsConfig = NewsConfig()
    exits: ExitsConfig = ExitsConfig()
    allocation: AllocationConfig = AllocationConfig()
    jobs: JobsConfig = JobsConfig()
    market_data: MarketDataConfig = MarketDataConfig()
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
