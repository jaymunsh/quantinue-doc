from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineRequest, RunStatus
from quantinue.orchestration.factory import build_default_orchestrator


@pytest.mark.anyio
async def test_pipeline_completes_all_roles_with_nvda_fixture() -> None:
    # Given
    orchestrator = build_default_orchestrator()
    request = PipelineRequest(
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
    )

    # When
    result = await orchestrator.run(request)

    # Then
    assert result.status is RunStatus.COMPLETED
    assert [stage.component for stage in result.stages] == [
        f"{index:02d}" for index in range(1, 12)
    ]
    assert result.order is not None
    assert result.order.status == "filled"
    assert result.review is not None
    assert result.review.outcome == "pending_t_plus_5"


@pytest.mark.anyio
async def test_pipeline_returns_existing_run_for_same_cycle() -> None:
    # Given
    orchestrator = build_default_orchestrator()
    request = PipelineRequest(
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
    )

    # When
    first = await orchestrator.run(request)
    second = await orchestrator.run(request)

    # Then
    assert second.run_id == first.run_id
    assert len(second.stages) == 11
