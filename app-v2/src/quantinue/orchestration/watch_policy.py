"""Configuration owned by the intraday streaming layer."""

from pydantic import BaseModel, ConfigDict, Field


class WatchStreamConfig(BaseModel):
    """Bound a live IEX stream to the Alpaca Basic-plan contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    symbol_limit: int = Field(default=30, gt=0, le=30)
    resubscribe_seconds: int = Field(default=60, gt=0, le=300)
    reconnect_seconds: int = Field(default=5, gt=0, le=60)
