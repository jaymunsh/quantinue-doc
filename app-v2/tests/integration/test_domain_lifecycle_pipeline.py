"""Pipeline-to-domain lifecycle integration without external providers."""

from datetime import UTC, datetime

import pytest

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator


class RecordingDomainStore(InMemoryRunStore):
    """Run-store fake that records the canonical domain lifecycle order."""

    def __init__(self) -> None:
        super().__init__()
        self.domain_calls: list[tuple[str, int | None, int | None]] = []
        self.signal_id: int | None = None
        self.account_id: int | None = None

    async def stage_completed(
        self,
        component: str,
        previous: PipelineContext,
        result: PipelineContext,
    ) -> PipelineContext:
        """Model real ID creation before risk and reuse after execution."""
        del previous
        if component == "08":
            self.signal_id = 701
            self.account_id = 41
        self.domain_calls.append((component, self.signal_id, self.account_id))
        return result


@pytest.mark.anyio
async def test_pipeline_invokes_domain_lifecycle_in_stage_order_with_ids() -> None:
    # Given
    store = RecordingDomainStore()
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), store=store)
    orchestrator = PipelineOrchestrator(roles, store)
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 13, 13, 0, tzinfo=UTC))

    # When
    result = await orchestrator.run(request)

    # Then
    assert [component for component, _, _ in store.domain_calls] == [
        f"{index:02d}" for index in range(1, 12)
    ]
    assert store.domain_calls[8] == ("09", 701, 41)
    assert store.domain_calls[9] == ("10", 701, 41)
    assert result.review is not None
