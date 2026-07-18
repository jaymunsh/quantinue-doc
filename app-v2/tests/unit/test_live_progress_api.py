from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, ClassVar

import anyio
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from quantinue.api.schemas import AsyncRunStart, ControlRoomRun
from quantinue.core.contracts import PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app
from quantinue.orchestration.pipeline import PipelineOrchestrator

if TYPE_CHECKING:
    from quantinue.core.contracts import PipelineContext


class DelayedRole:
    component: ClassVar[str] = "05"
    name: ClassVar[str] = "공시 분석"

    def __init__(self, entered: Event, release: anyio.Event, finished: Event) -> None:
        self._entered = entered
        self._release = release
        self._finished = finished

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self._entered.set()
        await self._release.wait()
        self._finished.set()
        return context.add_stage(self.component, self.name, "delayed completed")


class ShutdownRole:
    component: ClassVar[str] = "05"
    name: ClassVar[str] = "공시 분석"

    def __init__(self, entered: Event, cancelled: Event) -> None:
        self._entered = entered
        self._cancelled = cancelled

    async def execute(self, context: PipelineContext) -> PipelineContext:
        self._entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            self._cancelled.set()
        return context


async def _seed_noncanonical_active_attempt(store: InMemoryRunStore) -> None:
    request = PipelineRequest(
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, tzinfo=UTC),
    )
    claim = await store.claim("noncanonical-active", request)
    assert claim.context is not None
    _ = await store.start_attempt("noncanonical-active", "99", request.cycle_ts)


def test_async_launch_returns_immediately_and_projects_active_then_terminal_run() -> None:
    # Given
    entered = Event()
    release = anyio.Event()
    finished = Event()
    store = InMemoryRunStore()
    orchestrator = PipelineOrchestrator((DelayedRole(entered, release, finished),), store)
    app = create_app(store=store, orchestrator=orchestrator)

    # When
    with TestClient(app) as client:
        response = client.post("/api/runs/async", json={"ticker": "NVDA"})
        assert entered.wait(timeout=1)
        active = client.get("/api/runs")
        portal = client.portal
        assert portal is not None
        _ = portal.call(release.set)
        assert finished.wait(timeout=1)
        terminal = client.get("/api/runs")

    # Then
    assert response.status_code == 202
    assert AsyncRunStart.model_validate_json(response.content).accepted is True
    active_payload = TypeAdapter(list[ControlRoomRun]).validate_json(active.content)[0]
    terminal_payload = TypeAdapter(list[ControlRoomRun]).validate_json(terminal.content)[0]
    assert active_payload.status.value == "running"
    assert active_payload.current_stage is not None
    assert active_payload.current_stage.component == "05"
    assert active_payload.current_stage.name == "공시 분석"
    assert active_payload.current_stage.status.value == "running"
    assert active_payload.next_stage is not None
    assert active_payload.next_stage.component == "06"
    assert active_payload.next_stage.name == "뉴스 분석"
    assert active_payload.next_stage.status.value == "pending"
    assert terminal_payload.status.value == "completed"


def test_async_launch_reuses_one_background_task_for_a_duplicate_cycle() -> None:
    # Given
    entered = Event()
    release = anyio.Event()
    finished = Event()
    store = InMemoryRunStore()
    orchestrator = PipelineOrchestrator((DelayedRole(entered, release, finished),), store)
    app = create_app(store=store, orchestrator=orchestrator)

    # When
    with TestClient(app) as client:
        first = client.post("/api/runs/async", json={"ticker": "NVDA"})
        assert entered.wait(timeout=1)
        duplicate = client.post("/api/runs/async", json={"ticker": "NVDA"})
        portal = client.portal
        assert portal is not None
        _ = portal.call(release.set)
        assert finished.wait(timeout=1)

    # Then
    assert AsyncRunStart.model_validate_json(first.content).accepted is True
    assert AsyncRunStart.model_validate_json(duplicate.content).accepted is False
    assert len(anyio.run(store.list_recent)) == 1


def test_invalid_form_does_not_start_a_background_run() -> None:
    # Given
    app = create_app(store=InMemoryRunStore())

    # When
    with TestClient(app) as client:
        response = client.post("/runs", data={"ticker": "BAD!"}, follow_redirects=False)
        runs = client.get("/api/runs")

    # Then
    assert response.status_code == 303
    assert runs.json() == []


def test_app_shutdown_cancels_and_awaits_an_owned_pipeline_task() -> None:
    # Given
    entered = Event()
    cancelled = Event()
    store = InMemoryRunStore()
    app = create_app(
        store=store,
        orchestrator=PipelineOrchestrator((ShutdownRole(entered, cancelled),), store),
    )

    # When
    with TestClient(app) as client:
        response = client.post("/api/runs/async", json={"ticker": "NVDA"})
        assert entered.wait(timeout=1)

    # Then
    assert response.status_code == 202
    assert cancelled.wait(timeout=1)


def test_active_noncanonical_attempt_falls_back_without_breaking_safe_list_api() -> None:
    # Given
    store = InMemoryRunStore()
    anyio.run(_seed_noncanonical_active_attempt, store)
    app = create_app(store=store)

    # When
    with TestClient(app) as client:
        response = client.get("/api/runs")

    # Then
    assert response.status_code == 200
    payload = TypeAdapter(list[ControlRoomRun]).validate_json(response.content)[0]
    assert payload.current_stage is not None
    assert payload.current_stage.component == "01"
    assert payload.next_stage is not None
    assert payload.next_stage.component == "02"
    assert "error_message" not in response.text
