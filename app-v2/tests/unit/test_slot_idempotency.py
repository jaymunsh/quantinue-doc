"""E2E-2: two triggers inside one slot resolve to a single pipeline run."""

from datetime import UTC, datetime

import pytest

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator


@pytest.mark.anyio
async def test_same_slot_double_run_returns_single_run() -> None:
    store = InMemoryRunStore()
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), store=store)
    orchestrator = PipelineOrchestrator(roles, store)
    slot = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    request = PipelineRequest(ticker="NVDA", cycle_ts=slot)

    first = await orchestrator.run(request)
    second = await orchestrator.run(request)

    assert first.run_id == second.run_id  # 두 번째는 기존 런 반환 — 신규 락 불필요 검증
    assert await store.latest_cycle_ts() == slot
