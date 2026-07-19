"""Immutable role 07 strategy boundary contracts and code gates."""

# ruff: noqa: EM101, EM102, TRY003

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantinue.orchestration.policy import GatesConfig, ProfileConfig

MIN_CONVICTION = 0.6
# 프로필이 없을 때의 매도 문턱. 매수보다 낮은 이유는 비대칭이다 — 좋은 종목을
# 안 사면 기회를 놓칠 뿐이지만, 나쁜 종목을 안 팔면 손실이 계속 자란다.
MIN_SELL_CONVICTION = 0.6


class ContractViolationError(ValueError):
    """Typed role-07 boundary contract failure."""


ContractViolation = ContractViolationError


class StrategyInput(BaseModel):
    """Normalized upstream facts presented to the strategist."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    technical_score: float = Field(ge=0, le=1)
    disclosure_score: float | None = Field(ge=0, le=1)
    # 공시와 마찬가지로 부재는 기권이다. 뉴스가 없는 날에 0.5를 지어 넣으면
    # 그 가짜 중립값이 실제 판단을 희석한다 — 특히 매도 쪽에서, 논지가 무너진
    # 종목의 약세 확신을 끌어올려 팔지 못하게 만든다.
    news_score: float | None = Field(ge=0, le=1)
    is_daily_pick: bool
    source_trust: float = Field(default=1.0, ge=0, le=1)
    macro_risk_score: float = Field(default=0.0, ge=0, le=1)
    disclosure_hard_blocked: bool = False
    news_hard_blocked: bool = False
    disclosure_snapshot_at: datetime
    news_snapshot_at: datetime
    # 보유 맥락. 안 사는 것과 파는 것은 다른 판단이라, 이미 들고 있는지를
    # 모르면 07은 매도를 말할 자격이 없다. 0이면 미보유다.
    held_quantity: int = Field(default=0, ge=0)
    entry_price: float | None = Field(default=None, gt=0)
    business_days_held: int = Field(default=0, ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_future_snapshots(self) -> Self:
        """Prevent future information from entering a strategy decision."""
        if self.cycle_ts.tzinfo is None:
            raise ContractViolation("cycle_ts must include a timezone")
        for name, timestamp in (
            ("disclosure_snapshot_at", self.disclosure_snapshot_at),
            ("news_snapshot_at", self.news_snapshot_at),
        ):
            if timestamp.tzinfo is None:
                raise ContractViolation(f"{name} must include a timezone")
            if timestamp.astimezone(UTC) > self.cycle_ts.astimezone(UTC):
                raise ContractViolation(f"{name} must not be after cycle_ts")
        if any(not item.startswith(f"{self.run_id}:") for item in self.evidence_ids):
            raise ContractViolation("evidence must belong to the same run")
        if self.disclosure_hard_blocked and self.news_hard_blocked:
            raise ContractViolation("contradictory upstream state: both sources hard-blocked")
        return self

    @classmethod
    def fixture(cls, **changes: str | datetime | bool | float | tuple[str, ...]) -> Self:
        """Build an offline input whose snapshots meet the five-minute SLA."""
        now = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
        values = {
            "run_id": "fixture-run",
            "ticker": "NVDA",
            "cycle_ts": now,
            "technical_score": 0.82,
            "disclosure_score": 0.78,
            "news_score": 0.74,
            "is_daily_pick": True,
            "disclosure_snapshot_at": now - timedelta(minutes=1),
            "news_snapshot_at": now - timedelta(minutes=1),
            "evidence_ids": (
                "fixture-run:technical",
                "fixture-run:disclosure",
                "fixture-run:news",
            ),
        }
        return cls.model_validate({**values, **changes})

    def blockers(self, gates: GatesConfig | None = None) -> tuple[str, ...]:
        """Return deterministic blockers before any model recommendation.

        신선도 문턱이 config 소유인 이유: 같은 코드가 두 케이던스를 돈다.
        11단계 러너는 판단 직전에 증거를 받지만(분 단위), 일 1회 잡은 어제
        닫힌 세션을 본다(시간 단위). 5분을 코드에 박아두면 후자는 매일 전 종목이
        블록돼 아무것도 판단하지 못한다 — 실제로 그 리터럴이 배치 경로를
        구조적으로 막고 있었다.
        """
        selected = gates or GatesConfig()
        maximum_age = timedelta(minutes=selected.evidence_max_age_minutes)
        blockers: list[str] = []
        if self.disclosure_hard_blocked or self.news_hard_blocked:
            blockers.append("upstream_hard_block")
        cycle = self.cycle_ts.astimezone(UTC)
        if cycle - self.disclosure_snapshot_at.astimezone(UTC) > maximum_age:
            blockers.append("stale_disclosure_snapshot")
        if cycle - self.news_snapshot_at.astimezone(UTC) > maximum_age:
            blockers.append("stale_news_snapshot")
        if (
            gates is not None
            and self.disclosure_score is not None
            and self.disclosure_score <= gates.hard_negative_max
        ):
            # 강한 악재는 아무리 확신도가 높아도 매수를 막는다.
            blockers.append("hard_negative_sentiment")
        return tuple(blockers)


class StrategyOutput(BaseModel):
    """Code-gated strategist result; model output cannot bypass blockers."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", str_strip_whitespace=True)

    run_id: str
    ticker: str
    cycle_ts: datetime
    side: Literal["buy", "hold", "sell"]
    conviction: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    blockers: tuple[str, ...] = ()
    gate_passed: bool

    @model_validator(mode="after")
    def require_gate_proof_for_buy(self) -> Self:
        """Make direct buy construction without a passed code gate invalid.

        매도에는 같은 증명을 요구하지 않는다. 블로커는 "살 근거가 부족하다"는
        뜻인데 그건 파는 것을 막을 이유가 못 된다 — 오히려 파야 할 이유에
        가깝다. 매도의 안전장치는 "실제로 들고 있는가"이고 그건 from_model이 본다.
        """
        if self.side == "buy" and (not self.gate_passed or self.blockers):
            raise ContractViolation("buy requires code gate proof")
        return self

    @staticmethod
    def vote_conviction(
        source: StrategyInput, gates: GatesConfig, model_score: float | None = None
    ) -> float:
        """Average the surviving signal votes, then apply the macro deduction.

        A news score sourced below the trust floor loses its vote entirely
        rather than being down-weighted, so an unreliable outlet cannot lift a
        decision at all. An absent disclosure abstains for the same reason in
        reverse: silence is not bad news, and scoring it zero would both dilute
        conviction and trip the hard-negative gate.
        """
        votes = [source.technical_score]
        if source.disclosure_score is not None:
            votes.append(source.disclosure_score)
        if source.news_score is not None and source.source_trust >= gates.source_trust_min:
            votes.append(source.news_score)
        if model_score is not None:
            votes.append(model_score)
        raw = sum(votes) / len(votes)
        penalised = raw - gates.macro_penalty(source.macro_risk_score)
        return round(min(1.0, max(0.0, penalised)), 3)

    @staticmethod
    def vote_bearishness(
        source: StrategyInput, gates: GatesConfig, model_score: float | None = None
    ) -> float:
        """Score the case for **leaving** this position, not for entering it.

        **왜 확신도의 여집합이 아닌가.** 원래는 ``1 - conviction``이었는데,
        conviction에는 기술 랭킹 점수(``technical_score``)가 평균으로 섞여 있다.
        그런데 픽은 정의상 그 점수 상위에서 뽑히므로 보유 종목의 랭킹은 거의
        항상 높다 — 실측 0.94~0.96. 그러면 공격형 문턱(0.60)을 넘기려면 모델이
        **음수**를 내야 하고, 매도 경로가 산술적으로 닫힌다. 실 LLM으로 -23%
        포지션 3종목을 돌려 확인했다: 약세 확신이 최대 0.447에서 멈췄다.

        더 근본적으로, 랭킹이 답하는 질문은 "지금 사기 좋은가"이지 "계속 들고
        있어야 하는가"가 아니다. 논지가 무너졌는지를 묻는데 그 논지의 근거였던
        점수를 다시 평균에 넣으면, 자기 근거로 자기 붕괴를 반박하는 셈이다.
        보유 맥락(진입가·미실현손익·보유일)을 본 유일한 판단자는 모델이다.

        매크로 감점의 부호가 뒤집히는 것도 같은 이유다 — 확신도에서 빼는 값은
        하방 판정에서는 더해야 대칭이 맞는다.
        """
        if model_score is None:
            # 모델 없는 경로(구 러너 회귀)에서도 답은 있어야 한다. 있는 것으로만
            # 답하되, 그게 랭킹뿐이라는 사실이 곧 판단의 한계다.
            return round(min(1.0, max(0.0, 1.0 - source.technical_score)), 3)
        bearish = 1.0 - model_score + gates.macro_penalty(source.macro_risk_score)
        return round(min(1.0, max(0.0, bearish)), 3)

    @staticmethod
    def vote_consensus(
        source: StrategyInput,
        gates: GatesConfig,
        profile: ProfileConfig,
        model_score: float | None = None,
    ) -> int:
        """Count how many surviving votes cleared the buy threshold.

        Recorded for later study, never gated on. A vote stripped upstream —
        untrusted news, absent disclosure — cannot consent, because silence is
        not agreement.
        """
        votes = [source.technical_score]
        if source.disclosure_score is not None:
            votes.append(source.disclosure_score)
        if source.news_score is not None and source.source_trust >= gates.source_trust_min:
            votes.append(source.news_score)
        if model_score is not None:
            votes.append(model_score)
        return sum(1 for vote in votes if vote >= profile.buy_threshold)

    @classmethod
    def from_model(  # noqa: PLR0913 - each gate input is an explicit seam
        cls,
        source: StrategyInput,
        conviction: float,
        summary: str,
        *,
        gates: GatesConfig | None = None,
        profile: ProfileConfig | None = None,
        minimum_confidence: float = MIN_CONVICTION,
        bearishness: float | None = None,
    ) -> Self:
        """Apply hard gates after schema-valid model output."""
        blockers = source.blockers(gates)
        threshold = profile.buy_threshold if profile is not None else minimum_confidence
        can_buy = source.is_daily_pick and conviction >= threshold and not blockers
        # 약세 확신은 강세 확신의 여집합이 **아니다**(vote_bearishness 참조).
        # 부르는 쪽이 안 주면 여집합으로 떨어지는데, 그 경로는 기술 랭킹이
        # 섞인 값이라 보유 종목에서는 매도가 사실상 발동하지 않는다.
        if bearishness is None:
            bearishness = round(1.0 - conviction, 3)
        sell_threshold = (
            profile.sell_threshold if profile is not None else MIN_SELL_CONVICTION
        )
        # **보유하지 않으면 팔 수 없다.** 매도 판단의 유일한 하드 게이트다.
        # 하드 이벤트(상장폐지·거래정지)로 인한 매도는 여기가 아니라 청산 잡이
        # 판정한다 — 권위 있는 쪽은 SEC 폼이지 모델의 확신도가 아니다.
        can_sell = source.held_quantity > 0 and bearishness >= sell_threshold
        if can_sell:
            side = "sell"
        elif can_buy:
            side = "buy"
        else:
            side = "hold"
        return cls(
            run_id=source.run_id,
            ticker=source.ticker,
            cycle_ts=source.cycle_ts,
            side=side,
            conviction=conviction,
            summary=summary,
            evidence_ids=source.evidence_ids,
            blockers=blockers,
            gate_passed=can_buy or can_sell,
        )


StrategistInput = StrategyInput
StrategistOutput = StrategyOutput
