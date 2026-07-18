"""Typed role 10 input and output contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from quantinue.roles.role_09_risk_portfolio.evidence import LateStageEvidenceInput


class OrderExecutionInput(LateStageEvidenceInput):
    """Validated fixed-bracket plan accepted by role 10."""

    signal_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    quantity: int = Field(ge=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)

    @computed_field
    @property
    def client_order_id(self) -> str:
        """Derive an Alpaca-safe stable ID from the two uniqueness dimensions."""
        return f"q-a{self.account_id}-s{self.signal_id}"

    @computed_field
    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """Expose the validated lineage IDs carried into broker submission."""
        return tuple(item.evidence_id for item in self.evidence)

    @model_validator(mode="after")
    def require_buy_bracket(self) -> "OrderExecutionInput":
        """Reject inverted or zero-width buy brackets."""
        if not self.stop_loss < self.entry_price < self.take_profit:
            msg = "buy bracket must satisfy stop < entry < take-profit"
            raise ValueError(msg)
        return self


class OrderExecutionOutput(BaseModel):
    """Observable terminal or broker-accepted result from role 10."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    client_order_id: str
    broker_order_id: str | None
    status: Literal["skipped", "submitted", "accepted", "filled", "canceled", "rejected"]
    quantity: int = Field(ge=0)
    filled_avg_price: float = Field(ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
