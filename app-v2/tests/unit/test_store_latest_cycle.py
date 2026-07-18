"""RunStore.latest_cycle_ts: the scheduler's view of the newest useful cycle."""

from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.orchestration.lifecycle import deterministic_run_key


def _key(request: PipelineRequest) -> str:
    return str(deterministic_run_key(request.ticker, request.cycle_ts))


@pytest.mark.anyio
async def test_latest_cycle_ts_none_when_empty() -> None:
    store = InMemoryRunStore()
    await store.initialize()

    assert await store.latest_cycle_ts() is None


@pytest.mark.anyio
async def test_latest_cycle_ts_counts_completed_and_ignores_abandoned() -> None:
    store = InMemoryRunStore()
    await store.initialize()
    early = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 20, 13, 30, tzinfo=UTC))
    late = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 20, 14, 0, tzinfo=UTC))

    claim_early = await store.claim(_key(early), early)
    assert claim_early.acquired
    assert claim_early.context is not None
    await store.finish_run(_key(early), claim_early.context.to_run())

    claim_late = await store.claim(_key(late), late)
    assert claim_late.acquired
    await store.abandon(_key(late))  # interrupted → resumable, not a finished cycle

    assert await store.latest_cycle_ts() == early.cycle_ts


@pytest.mark.anyio
async def test_latest_cycle_ts_counts_active_running_claim() -> None:
    store = InMemoryRunStore()
    await store.initialize()
    running = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 20, 14, 30, tzinfo=UTC))

    claim = await store.claim(_key(running), running)
    assert claim.acquired

    assert await store.latest_cycle_ts() == running.cycle_ts
