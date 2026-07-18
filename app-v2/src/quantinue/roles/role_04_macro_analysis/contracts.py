"""Immutable input and output contracts for role 04."""

from typing import Final

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.core.ontology import Regime
from quantinue.core.schemas import AwareDateTime, ContractModel
from quantinue.roles.role_01_universe_screener.contracts import EvidenceBoundInput

RISK_ON_MAX: Final = 0.30
RISK_OFF_MIN: Final = 0.70


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
