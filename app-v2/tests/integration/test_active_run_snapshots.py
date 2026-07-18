import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from quantinue.core.contracts import PipelineRequest, RunStatus
from quantinue.db.contracts import AttemptFailure
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")


@pytest.mark.anyio
@pytest.mark.skipif(DATABASE_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_active_snapshot_uses_checkpoint_and_redacts_retry_message() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    request = PipelineRequest(ticker=f"A{uuid4().hex[:10]}".upper(), cycle_ts=datetime.now(UTC))
    key = f"active-snapshot-{uuid4().hex}"
    claim = await store.claim(key, request)
    assert claim.context is not None
    checkpoint_attempt = await store.start_attempt(key, "01", request.cycle_ts)
    context = claim.context.add_stage("01", "universe", "checkpoint complete")
    await store.complete_stage(key, context, checkpoint_attempt)
    retry_attempt = await store.start_attempt(key, "05", request.cycle_ts)
    await store.fail_attempt(
        key,
        retry_attempt,
        request.cycle_ts,
        AttemptFailure("retrying", "TRANSIENT_HTTP_FAILURE", "provider secret payload"),
    )

    # When
    snapshots = await store.list_active()

    # Then
    snapshot = next(item for item in snapshots if item.run_id == context.run_id)
    assert snapshot.status is RunStatus.RETRYING
    assert [stage.component for stage in snapshot.stages] == ["01"]
    assert [(item.component, item.status) for item in snapshot.attempts] == [
        ("01", "completed"),
        ("05", "retrying"),
    ]
    assert "provider secret payload" not in snapshot.model_dump_json()

    await store.finish_run(key, context.to_run())
    assert all(item.run_id != context.run_id for item in await store.list_active())
    await store.close()
