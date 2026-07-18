from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pytest
from typing_extensions import override

from quantinue.broker.contracts import OrderPlan
from quantinue.core.contracts import OrderResult, PipelineRequest
from quantinue.db.contracts import (
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    DailyOrderReservation,
)
from quantinue.db.memory import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.orchestration.policy import DEFAULT_PIPELINE_POLICY

_CYCLE = datetime(2026, 7, 13, 13, tzinfo=UTC)


class _RecordingBroker:
    def __init__(
        self,
        status: Literal[
            "accepted", "canceled", "failed", "filled", "planned", "rejected", "submitted"
        ],
    ) -> None:
        self._status = status
        self.plans: list[OrderPlan] = []

    async def submit(self, plan: OrderPlan) -> OrderResult:
        self.plans.append(plan)
        return OrderResult(
            order_id=f"test-{plan.client_order_id}",
            client_order_id=plan.client_order_id,
            status=self._status,
            quantity=plan.quantity,
            filled_avg_price=plan.entry_price if self._status == "filled" else 0,
        )


class _RecordingRunStore(InMemoryRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.reconciliations: list[tuple[str, AppOrderExposureStatus]] = []

    @override
    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        self.reconciliations.append((idempotency_key, status))
        return await super().reconcile_app_order_exposure(idempotency_key, status)


def _orchestrator(store: InMemoryRunStore, broker: _RecordingBroker) -> PipelineOrchestrator:
    policy = DEFAULT_PIPELINE_POLICY.model_copy(
        update={"daily_new_order_cap": 2, "max_app_order_exposure_usd": Decimal("1000.00")}
    )
    roles = build_roles(DeterministicAnalyzer(), broker, store=store, policy=policy)
    return PipelineOrchestrator(roles[:10], store, policy=policy)


@pytest.mark.anyio
async def test_role09_sizes_from_configured_thousand_dollar_app_exposure() -> None:
    # Given: the first-cycle policy allows at most 1,000 USD of planned app exposure.
    store = InMemoryRunStore()
    broker = _RecordingBroker("accepted")

    # When: the deterministic NVDA pipeline reaches Role 09.
    run = await _orchestrator(store, broker).run(PipelineRequest(ticker="NVDA", cycle_ts=_CYCLE))

    # Then: the deterministic price sizes to one share from the $1,000 budget.
    assert run.order is not None
    assert run.order.quantity == 1
    assert broker.plans[0].quantity == 1


@pytest.mark.anyio
async def test_role09_cap_rejection_skips_broker_submission_with_clear_summary() -> None:
    # Given: the account already has its whole app-owned planned-exposure budget reserved.
    store = InMemoryRunStore()
    broker = _RecordingBroker("accepted")
    reserved = await store.reserve_daily_new_order(
        DailyOrderReservation(
            account_id=1,
            trade_date=_CYCLE.date(),
            signal_id=999,
            idempotency_key="q-a1-s999",
            ticker="NVDA",
            quantity=1,
            entry_price=Decimal("1000.00"),
            stop_price=Decimal("850.00"),
            take_profit_price=Decimal("1200.00"),
            cap=2,
            max_app_order_exposure_usd=Decimal("1000.00"),
        )
    )
    assert reserved.outcome.value == "acquired"

    # When: a new pipeline run reaches Role 09.
    run = await _orchestrator(store, broker).run(PipelineRequest(ticker="NVDA", cycle_ts=_CYCLE))

    # Then: the denied app cap is visible and Role 10 has no broker side effect.
    assert run.order is None
    assert "계획 노출 한도" in run.stages[8].summary
    assert broker.plans == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("broker_status", "expected_exposure_status"),
    [
        ("accepted", AppOrderExposureStatus.SUBMITTED),
        ("submitted", AppOrderExposureStatus.SUBMITTED),
        ("filled", AppOrderExposureStatus.FILLED),
        ("rejected", AppOrderExposureStatus.FAILED),
        ("failed", AppOrderExposureStatus.FAILED),
        ("canceled", AppOrderExposureStatus.CANCELED),
        ("planned", AppOrderExposureStatus.PLANNED),
    ],
)
async def test_role10_reconciles_each_durable_broker_result_exactly_once(
    broker_status: Literal[
        "accepted", "canceled", "failed", "filled", "planned", "rejected", "submitted"
    ],
    expected_exposure_status: AppOrderExposureStatus,
) -> None:
    # Given: the real in-memory store and a broker that returns one durable result.
    store = _RecordingRunStore()
    broker = _RecordingBroker(broker_status)
    orchestrator = _orchestrator(store, broker)
    request = PipelineRequest(ticker="NVDA", cycle_ts=_CYCLE)

    # When: the same run is requested twice through the idempotent pipeline boundary.
    first = await orchestrator.run(request)
    replay = await orchestrator.run(request)

    # Then: the broker result maps once to the matching exposure lifecycle state.
    assert first.order is not None
    assert replay == first
    assert len(broker.plans) == 1
    assert first.order.status == expected_exposure_status.value
    assert store.reconciliations == [(first.order.client_order_id, expected_exposure_status)]
