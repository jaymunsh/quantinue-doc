"""Typed runtime policy loaded from the canonical pipeline YAML."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field

# pydantic이 런타임에 필드 타입을 해석하므로 타입 전용 임포트로 옮길 수 없다.
from quantinue.llm.budget import ModelPrice  # noqa: TC001
from quantinue.orchestration.watch_policy import WatchStreamConfig
from quantinue.roles.disclosure.insider import InsiderPolicy

UTC_ZONE = ZoneInfo("UTC")

if TYPE_CHECKING:
    from pathlib import Path


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
    # running으로 굳은 슬롯을 몇 분 뒤에 알릴지. 가장 긴 정상 잡(실 LLM 분석
    # x2 ≈ 15분)보다 넉넉해야 정상 실행을 굳음으로 오인하지 않는다.
    stuck_alert_minutes: int = Field(default=30, gt=0, le=1_440)
    cadences: dict[str, JobCadenceConfig] = Field(default_factory=dict)

    def cadence_for(self, job_name: str) -> JobCadenceConfig:
        """Return the declared cadence, or the default daily one."""
        return self.cadences.get(job_name, JobCadenceConfig())


class RejudgeConfig(BaseModel):
    """LLM rejudgement limits inside the regular-session watch loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    move_trigger_pct: float = Field(default=0.05, gt=0, le=1)
    cooldown_minutes: int = Field(default=30, gt=0, le=390)
    sweep_times_ny: tuple[str, ...] = ("10:00", "12:45", "15:15")
    sell_budget_reserve_ratio: float = Field(default=0.20, ge=0, le=1)


class WatchConfig(BaseModel):
    """Regular-session polling policy for the intraday watch runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    interval_minutes: int = Field(default=1, gt=0, le=60)
    session: Literal["regular"] = "regular"
    rejudge: RejudgeConfig = RejudgeConfig()
    stream: WatchStreamConfig = WatchStreamConfig()


class BudgetConfig(BaseModel):
    """Spending ceiling enforced before any billable model call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_llm_usd: float = Field(default=3.0, ge=0)
    # 모델별 요율. 비어 있는 기본값이 맞다 — 로컬 LLM은 실제로 공짜이고,
    # 요율이 필요한 것은 openai 모드뿐이다. 그 모드에서 선언이 빠지면
    # 기동이 거부된다(``require_pricing_for``).
    model_pricing: dict[str, ModelPrice] = Field(default_factory=dict)


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

    profiles: dict[str, ProfileConfig] = Field(default_factory=_default_profiles)
    gates: GatesConfig = GatesConfig()
    screening: ScreeningConfig = ScreeningConfig()
    disclosure: DisclosureConfig = DisclosureConfig()
    news: NewsConfig = NewsConfig()
    exits: ExitsConfig = ExitsConfig()
    allocation: AllocationConfig = AllocationConfig()
    jobs: JobsConfig = JobsConfig()
    watch: WatchConfig = WatchConfig()
    market_data: MarketDataConfig = MarketDataConfig()
    budget: BudgetConfig = BudgetConfig()
    insider: InsiderPolicy = InsiderPolicy()


def load_mvp2_config(path: Path) -> Mvp2Config:
    """Load the mvp2 block; an absent block yields safe defaults (disabled)."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    return Mvp2Config.model_validate(document.get("mvp2") or {})
