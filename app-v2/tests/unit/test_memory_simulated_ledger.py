from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import anyio
import pytest

from quantinue.core.contracts import PipelineRequest
from quantinue.db.domain_records import InsufficientSimulatedCashError
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.simulated_portfolio import (
    SimulatedFill,
    SimulatedOrder,
    SimulatedOrderStatus,
)

NOW = datetime(2026, 7, 14, 1, tzinfo=UTC)
OPENING_CASH = Decimal("1000000.00")


def _order() -> SimulatedOrder:
    return SimulatedOrder(
        order_id="mock-order-1",
        ticker="NVDA",
        quantity=2,
        reference_price=Decimal("100.00"),
        status=SimulatedOrderStatus.FILLED,
        created_at=NOW,
    )


def _fill() -> SimulatedFill:
    return SimulatedFill(
        fill_id="mock-order-1",
        order_id="mock-order-1",
        ticker="NVDA",
        quantity=2,
        price=Decimal("100.00"),
        filled_at=NOW,
    )


@pytest.mark.anyio
async def test_memory_ledger_applies_one_completed_local_buy_exactly_once() -> None:
    # Given
    store = InMemoryRunStore()

    # When
    await store.record_simulated_order(_order(), _fill())
    await store.record_simulated_order(_order(), _fill())

    # Then
    snapshot = await store.simulated_portfolio(OPENING_CASH)
    assert snapshot.account.current_cash == Decimal("999800.00")
    assert snapshot.positions[0].ticker == "NVDA"
    assert snapshot.positions[0].quantity == 2
    assert snapshot.fills == (_fill(),)


@pytest.mark.anyio
async def test_memory_ledger_records_rejection_without_changing_cash() -> None:
    # Given
    store = InMemoryRunStore()
    rejected = replace(_order(), status=SimulatedOrderStatus.REJECTED)

    # When
    await store.record_simulated_order(rejected, None)

    # Then
    snapshot = await store.simulated_portfolio(OPENING_CASH)
    assert snapshot.account.current_cash == OPENING_CASH
    assert snapshot.positions == ()
    assert snapshot.orders == (rejected,)


@pytest.mark.anyio
async def test_memory_ledger_concurrent_same_fill_identity_is_atomic() -> None:
    # Given
    store = InMemoryRunStore()

    async def record() -> None:
        await store.record_simulated_order(_order(), _fill())

    # When
    async with anyio.create_task_group() as group:
        _ = group.start_soon(record)
        _ = group.start_soon(record)

    # Then
    snapshot = await store.simulated_portfolio(OPENING_CASH)
    assert snapshot.account.current_cash == Decimal("999800.00")
    assert len(snapshot.orders) == 1
    assert len(snapshot.fills) == 1


@pytest.mark.anyio
async def test_memory_portfolio_uses_latest_completed_run_mark() -> None:
    # Given
    store = InMemoryRunStore()
    await store.record_simulated_order(_order(), _fill())
    for hour, price in ((2, 110.0), (3, 125.0)):
        cycle_ts = datetime(2026, 7, 14, hour, tzinfo=UTC)
        request = PipelineRequest(ticker="NVDA", cycle_ts=cycle_ts)
        key = f"run-{hour}"
        claim = await store.claim(key, request)
        assert claim.context is not None
        context = replace(claim.context, last_price=price)
        attempt = await store.start_attempt(key, "01", cycle_ts)
        await store.complete_stage(key, context, attempt)
        await store.finish_run(key, context.to_run())

    # When
    snapshot = await store.simulated_portfolio(OPENING_CASH)

    # Then
    assert snapshot.positions[0].mark.price == Decimal("125.0")
    assert snapshot.positions[0].mark.as_of == datetime(2026, 7, 14, 3, tzinfo=UTC)


@pytest.mark.anyio
async def test_memory_ledger_rejects_insufficient_cash_without_partial_order_or_fill() -> None:
    # Given
    store = InMemoryRunStore(opening_cash=Decimal("100.00"))

    # When / Then
    with pytest.raises(InsufficientSimulatedCashError):
        await store.record_simulated_order(_order(), _fill())
    snapshot = await store.simulated_portfolio(Decimal("100.00"))
    assert snapshot.account.current_cash == Decimal("100.00")
    assert snapshot.orders == ()
    assert snapshot.fills == ()
