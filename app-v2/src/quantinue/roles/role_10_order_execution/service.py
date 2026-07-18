"""Submit one idempotent bracket order through the broker boundary."""

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import ClassVar

from quantinue.broker.mock import MockBroker
from quantinue.broker.provider import Broker, OrderPlan
from quantinue.core.contracts import PipelineContext
from quantinue.core.errors import ValidationFailureError
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.db.contracts import AppOrderExposureStatus, RunStore
from quantinue.db.simulated_portfolio import (
    SimulatedFill,
    SimulatedOrder,
    SimulatedOrderRecorder,
    SimulatedOrderStatus,
)
from quantinue.roles.role_09_risk_portfolio.evidence import evidence_from_pipeline_traces
from quantinue.roles.role_10_order_execution.contracts import OrderExecutionInput


@dataclass(frozen=True, slots=True)
class OrderExecution:
    """Order adapter consumer with a deterministic client order id."""

    broker: Broker
    store: RunStore
    component: ClassVar[str] = "10"
    name: ClassVar[str] = "주문·체결"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Submit a paper bracket or simulate it in mock mode."""
        quantity = require_value(context.quantity, component=self.component, field_name="quantity")
        stop_loss = require_value(
            context.stop_loss, component=self.component, field_name="stop_loss"
        )
        take_profit = require_value(
            context.take_profit, component=self.component, field_name="take_profit"
        )
        if quantity == 0:
            prefix = "로컬 모의 처리" if isinstance(self.broker, MockBroker) else "브로커 처리"
            return context.add_stage(self.component, self.name, f"{prefix} · 주문 생략, 0주")
        entry_price = require_value(
            context.last_price, component=self.component, field_name="last_price"
        )
        signal_key = f"{context.request.ticker}:{context.request.cycle_ts.isoformat()}".encode()
        signal_id = context.signal_id or int(sha256(signal_key).hexdigest()[:8], 16) + 1
        account_id = context.account_id or 1
        request = OrderExecutionInput(
            run_id=context.run_id,
            execution_at=context.request.cycle_ts,
            evidence=evidence_from_pipeline_traces(context, ("08", "09")),
            signal_id=signal_id,
            account_id=account_id,
            ticker=context.request.ticker,
            cycle_ts=context.request.cycle_ts,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        plan = OrderPlan(
            ticker=request.ticker,
            client_order_id=request.client_order_id,
            quantity=request.quantity,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
        )
        broker_order = await self.broker.submit(plan)
        order_status = _app_order_exposure_status(broker_order.status)
        order = broker_order.model_copy(update={"status": order_status.value})
        _ = await self.store.reconcile_app_order_exposure(
            request.client_order_id,
            order_status,
        )
        if isinstance(self.broker, MockBroker) and isinstance(self.store, SimulatedOrderRecorder):
            simulated_status = _simulated_order_status(order.status)
            simulated_order = SimulatedOrder(
                order_id=order.order_id,
                ticker=request.ticker,
                quantity=order.quantity,
                reference_price=Decimal(str(request.entry_price)),
                status=simulated_status,
                created_at=request.execution_at,
            )
            fill = (
                SimulatedFill(
                    fill_id=order.order_id,
                    order_id=order.order_id,
                    ticker=request.ticker,
                    quantity=order.quantity,
                    price=Decimal(str(order.filled_avg_price)),
                    filled_at=request.execution_at,
                )
                if simulated_status is SimulatedOrderStatus.FILLED
                else None
            )
            await self.store.record_simulated_order(simulated_order, fill)
        updated = replace(context, order=order)
        evidence = Evidence(
            evidence_id=f"{context.run_id}:10:order:{order.order_id}",
            run_id=context.run_id,
            source="broker-result",
            source_ref=f"broker://order/{order.order_id}",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.BROKER,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
        )
        prefix = "로컬 모의 체결" if isinstance(self.broker, MockBroker) else "Alpaca Paper 체결"
        return updated.add_stage(
            self.component,
            self.name,
            f"{prefix} · {order.status}, {order.quantity}주",
            evidence=evidence,
        )


def _app_order_exposure_status(status: str) -> AppOrderExposureStatus:
    match status:
        case "submitted" | "accepted":
            return AppOrderExposureStatus.SUBMITTED
        case "filled":
            return AppOrderExposureStatus.FILLED
        case "canceled":
            return AppOrderExposureStatus.CANCELED
        case "rejected" | "failed":
            return AppOrderExposureStatus.FAILED
        case "planned":
            return AppOrderExposureStatus.PLANNED
        case unexpected:
            field = "broker_order_status"
            raise ValidationFailureError(field, unexpected)


def _simulated_order_status(status: str) -> SimulatedOrderStatus:
    try:
        return SimulatedOrderStatus(status)
    except ValueError:
        field = "simulated_order_status"
        raise ValidationFailureError(field, status) from None
