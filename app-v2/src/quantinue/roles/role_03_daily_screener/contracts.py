"""Immutable input and output contracts for role 03."""

from datetime import date

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.core.ontology import Bucket
from quantinue.core.schemas import ContractModel
from quantinue.roles.role_01_universe_screener.contracts import EvidenceBoundInput
from quantinue.roles.role_02_technical_analysis.contracts import TechnicalSnapshot


class DailyScreenerInput(EvidenceBoundInput):
    """Role 03 boundary for the daily technical population."""

    trade_date: date | None = None
    universe_as_of: date | None = None
    snapshots: tuple[TechnicalSnapshot, ...] = ()


class DailyPick(ContractModel):
    """Documented tb_daily_pick row."""

    trade_date: date
    ticker: str = Field(min_length=1, max_length=12)
    universe_as_of: date
    bucket: Bucket
    rank: int = Field(ge=1, le=50)
    sector: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    is_requested_focus: bool = False
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class DailyScreenerOutput(ContractModel):
    """At most fifty unique picks ranked across all buckets."""

    run_id: str = Field(min_length=1)
    picks: tuple[DailyPick, ...] = Field(max_length=50)

    @model_validator(mode="after")
    def require_unique_tickers_and_ranks(self) -> "DailyScreenerOutput":
        """Reject duplicate securities or ambiguous final ranks."""
        tickers = tuple(item.ticker for item in self.picks)
        ranks = tuple(item.rank for item in self.picks)
        if len(tickers) != len(set(tickers)) or len(ranks) != len(set(ranks)):
            code = "duplicate_daily_pick"
            message = "daily picks must have unique tickers and ranks"
            raise PydanticCustomError(code, message)
        return self
