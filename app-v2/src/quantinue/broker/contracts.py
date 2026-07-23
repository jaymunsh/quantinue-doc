"""Broker-independent order plan and adapter protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ClosePlan(BaseModel):
    """Validated exit passed to every broker implementation.

    OrderPlan과 별도 모델인 이유: 청산은 stop/take_profit이 **없는** 것이
    정상인데 OrderPlan은 셋 다 필수이고 삼중 순서까지 강제한다. 청산을 그
    모델에 욱여넣으려면 더미 값을 채워야 하고, 그 순간 원장은 "이 청산의
    손절가는 얼마였나"에 지어낸 숫자로 답하게 된다.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str = Field(min_length=1, max_length=12)
    client_order_id: str = Field(min_length=1, max_length=48)
    quantity: int = Field(gt=0)
    # 매수의 entry_price에 대응하는 "판단 시점 기준가". 시뮬에서는 체결가가 되고,
    # 주문 어댑터에서는 청산 가격 산정의 기준이 된다.
    reference_price: float = Field(gt=0)
    # 어느 진입을 닫는가 — 실현손익의 짝. tb_order.closes_order_id와 같은 뜻이다.
    closes_client_order_id: str = Field(min_length=1, max_length=48)


class Broker(Protocol):
    """Minimal common capability consumed by role 10."""

    async def submit(self, plan: OrderPlan) -> OrderResult:
        """Submit or simulate exactly one bracket order."""
        ...


@runtime_checkable
class ClosingBroker(Protocol):
    """Optional capability: can this adapter exit a position?

    어댑터마다 브래킷 보호 주문 취소와 청산 주문을 지원하는 범위가 다르다.
    능력 광고 패턴을 쓰면 청산 잡이 "닫을 수 있는 브로커에게만" 물어보게
    되어, 못 닫는 브로커에 붙었을 때 조용히 실패하는 대신 명시적으로
    건너뛴다.
    """

    async def close(self, plan: ClosePlan) -> OrderResult:
        """Exit an existing position, cancelling any resting protective legs."""
        ...


@runtime_checkable
class TradabilityBroker(Protocol):
    """Optional pre-submit capability: can this symbol be traded right now?"""

    async def is_tradable(self, ticker: str) -> bool:
        """Return whether the venue currently accepts orders for this symbol."""
        ...


# 구 core/terminal_run_types에서 이주했다 — 그 모듈은 11단계 런의 표현이라
# 러너와 함께 죽었지만, 주문 결과 계약은 브로커의 것이라 여기 산다.
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
