"""Terminal result models retained alongside a completed pipeline run."""

from pydantic import BaseModel, ConfigDict


class OrderResult(BaseModel):
    """Normalized order result independent of broker implementation."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    client_order_id: str
    status: str
    quantity: int
    filled_avg_price: float
    parent_order_id: str | None = None
    stop_leg_order_id: str | None = None
    take_profit_leg_order_id: str | None = None


class ReviewResult(BaseModel):
    """T+5 review placeholder created after a fill."""

    model_config = ConfigDict(frozen=True)

    outcome: str
    summary: str
