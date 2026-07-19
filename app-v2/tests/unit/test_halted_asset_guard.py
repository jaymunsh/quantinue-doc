"""A halted or delisted symbol must never reach the broker.

Alpaca rejects orders on non-tradable assets, but a rejection arrives after the
order exists in our ledger. Asking first keeps the skip observable and keeps
tb_order free of orders that could never have filled.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from quantinue.broker.mock import MockBroker
from quantinue.broker.provider import OrderPlan
from quantinue.core.contracts import OrderResult, PipelineContext, PipelineRequest
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.db.memory import InMemoryRunStore
from quantinue.roles.role_10_order_execution.service import OrderExecution

NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


class _TradabilityBroker:
    """Broker that answers tradability and records what it was asked to submit."""

    def __init__(self, *, tradable: bool) -> None:
        self._tradable = tradable
        self._inner = MockBroker()
        self.submitted: list[OrderPlan] = []
        self.asked: list[str] = []

    async def is_tradable(self, ticker: str) -> bool:
        self.asked.append(ticker)
        return self._tradable

    async def submit(self, plan: OrderPlan) -> OrderResult:
        self.submitted.append(plan)
        return await self._inner.submit(plan)


def _context() -> PipelineContext:
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))
    stages = context
    for component in ("08", "09"):
        evidence = Evidence(
            evidence_id=f"{context.run_id}:{component}:plan",
            run_id=context.run_id,
            source="policy",
            source_ref=f"policy://{component}/v1",
            observed_at=NOW,
            captured_at=NOW,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
        )
        stages = stages.add_stage(component, component, "ok", evidence=evidence)
    return replace(
        stages,
        last_price=100.0,
        quantity=3,
        stop_loss=85.0,
        take_profit=120.0,
    )


@pytest.mark.anyio
async def test_halted_symbol_is_never_submitted() -> None:
    broker = _TradabilityBroker(tradable=False)
    service = OrderExecution(broker, InMemoryRunStore())

    updated = await service.execute(_context())

    assert broker.asked == ["NVDA"]
    assert broker.submitted == []
    assert updated.order_skipped_reason == "not_tradable"


@pytest.mark.anyio
async def test_tradable_symbol_is_submitted_normally() -> None:
    broker = _TradabilityBroker(tradable=True)
    service = OrderExecution(broker, InMemoryRunStore())

    updated = await service.execute(_context())

    assert broker.asked == ["NVDA"]
    assert len(broker.submitted) == 1
    assert updated.order_skipped_reason is None


@pytest.mark.anyio
async def test_broker_without_tradability_support_still_submits() -> None:
    # A broker predating the check must not be silently disabled by it.
    broker = MockBroker()
    service = OrderExecution(broker, InMemoryRunStore())

    updated = await service.execute(_context())

    assert updated.order_skipped_reason is None
    assert "거래 불가" not in updated.stages[-1].summary


@pytest.mark.anyio
async def test_tradability_is_not_queried_for_a_zero_quantity_plan() -> None:
    broker = _TradabilityBroker(tradable=True)
    service = OrderExecution(broker, InMemoryRunStore())

    await service.execute(replace(_context(), quantity=0))

    assert broker.asked == []
