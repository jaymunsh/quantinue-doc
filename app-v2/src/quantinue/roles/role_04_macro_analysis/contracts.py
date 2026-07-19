"""Immutable input and output contracts for role 04."""

from typing import Final

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.core.ontology import Regime
from quantinue.core.schemas import AwareDateTime, ContractModel
from quantinue.roles.role_01_universe_screener.contracts import EvidenceBoundInput

RISK_ON_MAX: Final = 0.30
RISK_OFF_MIN: Final = 0.70

# 금리를 위험 점수로 접는 분모. 12%를 "완전한 위험회피"로 보는 MVP 근사다.
RATE_RISK_DIVISOR: Final = 12.0


# VIX·지수 수익률·달러의 MVP 기준값 — 실제로 수집하는 시리즈는 DFF 하나뿐이라
# 나머지 컬럼은 이 값으로 채운다(구 러너 role_04와 동일). 컬럼을 지어내는 게
# 아니라 "판단에 안 쓰는 값"임을 한 곳에 못박는 것이다.
MVP_BASELINE_VIX: Final = 18.2
MVP_BASELINE_NASDAQ_RET: Final = 0.2
MVP_BASELINE_SP500_RET: Final = 0.1
MVP_BASELINE_DOLLAR: Final = 104.3


def regime_from_rate(rate: float) -> tuple[Regime, float]:
    """Fold the fed funds rate into (regime, risk_score) by the shared thresholds.

    한 곳에 두는 이유: 구 러너(role_04 service)와 매크로 잡이 같은 산식을
    써야 한다. 복사하면 한쪽만 고쳐지는 날이 오고, 그때 두 경로가 같은
    금리에서 다른 국면을 내는데 어느 쪽이 옳은지 답할 수 없다.
    """
    risk_score = min(1.0, max(0.0, rate / RATE_RISK_DIVISOR))
    if risk_score <= RISK_ON_MAX:
        return Regime.RISK_ON, risk_score
    if risk_score >= RISK_OFF_MIN:
        return Regime.RISK_OFF, risk_score
    return Regime.NEUTRAL, risk_score


class MacroAnalysisInput(EvidenceBoundInput):
    """Role 04 market-wide observations at a planned hourly slot."""

    vix: float | None = Field(default=None, ge=0)
    nasdaq_ret: float | None = None
    sp500_ret: float | None = None
    rate: float | None = Field(default=None, ge=0)
    dollar: float | None = Field(default=None, gt=0)


class MacroAnalysisOutput(ContractModel):
    """Documented tb_macro row with evidence lineage."""

    run_id: str = Field(min_length=1)
    as_of: AwareDateTime
    regime: Regime
    risk_score: float = Field(ge=0, le=1)
    vix: float = Field(ge=0)
    nasdaq_ret: float
    sp500_ret: float
    rate: float = Field(ge=0)
    dollar: float = Field(gt=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_regime_matches_score(self) -> "MacroAnalysisOutput":
        """Reject a label that contradicts the documented score thresholds."""
        if self.risk_score <= RISK_ON_MAX:
            expected = Regime.RISK_ON
        elif self.risk_score >= RISK_OFF_MIN:
            expected = Regime.RISK_OFF
        else:
            expected = Regime.NEUTRAL
        if self.regime is not expected:
            code = "contradictory_macro_regime"
            message = "macro regime contradicts risk_score"
            raise PydanticCustomError(code, message)
        return self
