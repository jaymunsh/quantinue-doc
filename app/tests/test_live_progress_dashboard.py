from datetime import UTC, datetime

import anyio
from fastapi.testclient import TestClient

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app


async def _seed_active_run(store: InMemoryRunStore) -> None:
    request = PipelineRequest(
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    claim = await store.claim("active-dashboard", request)
    assert claim.context is not None
    _ = await store.start_attempt("active-dashboard", "05", request.cycle_ts)


async def _seed_same_minute_terminal_and_active_runs(store: InMemoryRunStore) -> None:
    request = PipelineRequest(
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, 9, 0, tzinfo=UTC),
    )
    terminal_claim = await store.claim("terminal-dashboard", request)
    assert terminal_claim.context is not None
    await store.finish_run("terminal-dashboard", PipelineContext(request=request).to_run())
    active_claim = await store.claim("active-dashboard", request)
    assert active_claim.context is not None
    _ = await store.start_attempt("active-dashboard", "05", request.cycle_ts)


def test_dashboard_renders_safe_live_progress_panel_only_for_active_runs() -> None:
    # Given
    store = InMemoryRunStore()
    anyio.run(_seed_active_run, store)
    app = create_app(store=store)

    # When
    with TestClient(app) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert 'id="live-pipeline"' in response.text
    assert "data-live-run-id=" in response.text
    assert "05 공시 분석" in response.text
    assert "06 뉴스 분석" in response.text
    assert 'aria-live="polite"' in response.text
    assert 'fetch("/api/runs"' in response.text
    assert "error_message" not in response.text


def test_terminal_dashboard_does_not_include_live_polling_script() -> None:
    # Given
    app = create_app(store=InMemoryRunStore())

    # When
    with TestClient(app) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert 'id="live-pipeline"' not in response.text
    assert "data-live-run-id" not in response.text


def test_dashboard_prefers_an_active_same_minute_run_over_terminal_history() -> None:
    # Given
    store = InMemoryRunStore()
    anyio.run(_seed_same_minute_terminal_and_active_runs, store)
    app = create_app(store=store)

    # When
    with TestClient(app) as client:
        dashboard = client.get("/")
        runs = client.get("/api/runs")

    # Then
    assert dashboard.status_code == 200
    assert 'id="live-pipeline"' in dashboard.text
    assert runs.json()[0]["status"] == "running"
    assert runs.json()[1]["status"] == "completed"
