"""Broker-independent order plan and adapter protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from quantinue.core.contracts import OrderResult


class OrderPlan(BaseModel):
    """Validated fixed buy bracket passed to every broker implementation."""

    model_config = ConfigDict(frozen=True)

    ticker: str = Field(min_length=1, max_length=12)
    client_order_id: str = Field(min_length=1, max_length=48)
    quantity: int = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)

    @model_validator(mode="after")
    def require_buy_bracket(self) -> OrderPlan:
        """Reject an inverted bracket before any adapter can submit it."""
        if not self.stop_loss < self.entry_price < self.take_profit:
            msg = "buy bracket must satisfy stop < entry < take-profit"
            raise ValueError(msg)
        return self


class Broker(Protocol):
    """Minimal common capability consumed by role 10."""

    async def submit(self, plan: OrderPlan) -> OrderResult:
        """Submit or simulate exactly one bracket order."""
        ...
