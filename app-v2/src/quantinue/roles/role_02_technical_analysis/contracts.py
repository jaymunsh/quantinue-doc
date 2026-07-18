"""Immutable input and output contracts for role 02."""

from datetime import date

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from quantinue.core.ontology import Trend
from quantinue.core.schemas import ContractModel
from quantinue.roles.role_01_universe_screener.contracts import EvidenceBoundInput


class Candle(ContractModel):
    """One completed daily OHLCV candle."""

    trade_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)

    @model_validator(mode="after")
    def require_valid_range(self) -> "Candle":
        """Reject prices that contradict the daily high-low range."""
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            code = "contradictory_ohlc_range"
            message = "OHLC candle range is contradictory"
            raise PydanticCustomError(code, message)
        return self


class TechnicalAnalysisInput(EvidenceBoundInput):
    """Role 02 boundary for universe candles and benchmark candles."""

    trade_date: date | None = None
    ticker: str | None = Field(default=None, min_length=1, max_length=12)
    candles: tuple[Candle, ...] = ()
    benchmark_candles: tuple[Candle, ...] = ()


class TechnicalSnapshot(ContractModel):
    """Documented tb_technical row."""

    trade_date: date
    ticker: str = Field(min_length=1, max_length=12)
    close: float = Field(gt=0)
    rs_20: float
    vol_ratio: float = Field(ge=0)
    ret_5d: float
    ret_20d: float
    atr_pct: float = Field(ge=0)
    high_252_ratio: float = Field(ge=0)
    rsi: float = Field(ge=0, le=100)
    macd: float
    ma20: float = Field(gt=0)
    ma50: float = Field(gt=0)
    trend: Trend
    ml_probs: dict[str, float] = Field(default_factory=dict)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class TechnicalAnalysisOutput(ContractModel):
    """Role 02 calculated snapshots and explicit exclusions."""

    run_id: str = Field(min_length=1)
    snapshots: tuple[TechnicalSnapshot, ...]
    excluded_insufficient_history: tuple[str, ...] = ()
