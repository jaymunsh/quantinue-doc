"""Typed input and output contracts for deterministic portfolio risk."""

from datetime import datetime
from math import floor
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantinue.roles.role_09_risk_portfolio.evidence import LateStageEvidenceInput

RISK_FRACTION: Final = 0.04
STOP_FRACTION: Final = 0.15
POSITION_CAP_FRACTION: Final = 0.25
TAKE_PROFIT_FRACTION: Final = 0.20


class RiskPortfolioInput(LateStageEvidenceInput):
    """Trusted inputs required by role 09's hard gates and sizing rule."""

    signal_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    ticker: str = Field(min_length=1, max_length=12)
    cycle_ts: datetime
    critic_approved: bool
    current_price: float = Field(ge=0.04)
    equity: float = Field(gt=0)
    has_position: bool = False
    has_open_order: bool = False
    event_within_two_days: bool = False
    daily_new_order_count: int = Field(default=0, ge=0)
    daily_new_order_cap: int = Field(default=5, ge=1)
    risk_score: float = Field(default=0, ge=0, le=1)


class RiskPortfolioOutput(BaseModel):
    """Role 09 result, including observable skip decisions."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    signal_id: int
    account_id: int
    ticker: str
    decision: Literal["planned", "skipped"]
    quantity: int = Field(ge=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    skipped_reason: (
        Literal[
            "critic_rejected",
            "event_window",
            "existing_position",
            "open_order",
            "insufficient_equity",
            "daily_order_cap",
            "risk_limit",
        ]
        | None
    )
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_decision(self) -> "RiskPortfolioOutput":
        """Keep planned and skipped states mutually exclusive."""
        if not self.stop_loss < self.entry_price < self.take_profit:
            msg = "buy bracket must satisfy stop < entry < take-profit"
            raise ValueError(msg)
        is_planned = self.decision == "planned"
        if is_planned != (self.quantity > 0 and self.skipped_reason is None):
            msg = "planned requires positive quantity; skipped requires a reason"
            raise ValueError(msg)
        return self


def build_order_plan(
    request: RiskPortfolioInput,
    stop_loss_ratio: float = STOP_FRACTION,
    take_profit_ratio: float = TAKE_PROFIT_FRACTION,
    maximum_risk_score: float = 1.0,
) -> RiskPortfolioOutput:
    """Apply hard gates then size by risk budget subject to the position cap."""
    reason: (
        Literal[
            "critic_rejected",
            "event_window",
            "existing_position",
            "open_order",
            "insufficient_equity",
            "daily_order_cap",
            "risk_limit",
        ]
        | None
    ) = None
    if not request.critic_approved:
        reason = "critic_rejected"
    elif request.risk_score > maximum_risk_score:
        reason = "risk_limit"
    elif request.event_within_two_days:
        reason = "event_window"
    elif request.has_position:
        reason = "existing_position"
    elif request.has_open_order:
        reason = "open_order"
    elif request.daily_new_order_count >= request.daily_new_order_cap:
        reason = "daily_order_cap"

    risk_budget_allocation = request.equity * RISK_FRACTION / stop_loss_ratio
    capped_allocation = min(risk_budget_allocation, request.equity * POSITION_CAP_FRACTION)
    quantity = floor(capped_allocation / request.current_price) if reason is None else 0
    if quantity == 0 and reason is None:
        reason = "insufficient_equity"
    return RiskPortfolioOutput(
        run_id=request.run_id,
        signal_id=request.signal_id,
        account_id=request.account_id,
        ticker=request.ticker,
        decision="planned" if quantity > 0 else "skipped",
        quantity=quantity,
        entry_price=request.current_price,
        stop_loss=round(request.current_price * (1 - stop_loss_ratio), 2),
        take_profit=round(request.current_price * (1 + take_profit_ratio), 2),
        skipped_reason=reason,
        evidence_ids=tuple(item.evidence_id for item in request.evidence),
    )
