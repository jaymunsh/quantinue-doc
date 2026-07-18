from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineRequest, RunStatus
from quantinue.db.contracts import AttemptFailure
from quantinue.db.memory import InMemoryRunStore


@pytest.mark.anyio
async def test_active_snapshot_combines_checkpoint_and_running_attempt_without_raw_error() -> None:
    # Given
    store = InMemoryRunStore()
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 13, tzinfo=UTC))
    claim = await store.claim("active-NVDA", request)
    assert claim.context is not None
    completed = await store.start_attempt("active-NVDA", "01", request.cycle_ts)
    context = claim.context.add_stage("01", "universe", "universe checkpoint")
    await store.complete_stage("active-NVDA", context, completed)
    running = await store.start_attempt("active-NVDA", "05", request.cycle_ts)

    # When
    snapshots = await store.list_active()

    # Then
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.run_id == context.run_id
    assert snapshot.ticker == "NVDA"
    assert snapshot.status is RunStatus.RUNNING
    assert [stage.component for stage in snapshot.stages] == ["01"]
    assert [(item.component, item.status) for item in snapshot.attempts] == [
        ("01", "completed"),
        ("05", "running"),
    ]
    assert running.error_message is None
    assert "error_message" not in snapshot.model_dump_json()


@pytest.mark.anyio
async def test_active_snapshot_reports_retrying_without_raw_failure_message() -> None:
    # Given
    store = InMemoryRunStore()
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 13, tzinfo=UTC))
    claim = await store.claim("retrying-NVDA", request)
    assert claim.context is not None
    attempt = await store.start_attempt("retrying-NVDA", "06", request.cycle_ts)
    await store.fail_attempt(
        "retrying-NVDA",
        attempt,
        request.cycle_ts,
        AttemptFailure("retrying", "TRANSIENT_HTTP_FAILURE", "provider secret payload"),
    )

    # When
    snapshots = await store.list_active()

    # Then
    assert snapshots[0].status is RunStatus.RETRYING
    assert snapshots[0].attempts[0].error_code == "TRANSIENT_HTTP_FAILURE"
    assert "provider secret payload" not in snapshots[0].model_dump_json()


@pytest.mark.anyio
async def test_active_snapshot_excludes_terminal_runs() -> None:
    # Given
    store = InMemoryRunStore()
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 13, tzinfo=UTC))
    claim = await store.claim("terminal-NVDA", request)
    assert claim.context is not None
    await store.finish_run("terminal-NVDA", claim.context.to_run())

    # When
    snapshots = await store.list_active()

    # Then
    assert snapshots == ()
    assert len(await store.list_recent()) == 1
