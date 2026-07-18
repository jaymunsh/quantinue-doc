"""FastAPI application and server-rendered control room."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import anyio
from fastapi import FastAPI, Form, Header, HTTPException, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from quantinue.api.access import ControlRoomAccess
from quantinue.api.live_runtime import LiveRunRuntime
from quantinue.api.presentation import control_room_run, simulated_portfolio_view
from quantinue.api.review_runtime import ReviewRuntime
from quantinue.api.reviews import build_review_router
from quantinue.api.run_detail import build_run_detail_router
from quantinue.api.schemas import (
    AsyncRunStart,
    ControlRoomRun,
    HealthResponse,
    RunCreate,
    SimulatedPortfolioView,
)
from quantinue.core.config import DatabaseMode, Settings
from quantinue.core.contracts import PipelineRequest, PipelineRun, RunStatus
from quantinue.core.logging import configure_logging
from quantinue.db.contracts import PersistedAttempt
from quantinue.orchestration.factory import (
    build_configured_orchestrator,
    build_default_orchestrator,
    build_market_data,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from quantinue.db.contracts import RunStore
    from quantinue.orchestration.pipeline import PipelineOrchestrator

PACKAGE_DIR = Path(__file__).parent
DASHBOARD_CSS = (PACKAGE_DIR / "web" / "static" / "dashboard.css").read_text(encoding="utf-8")


def _pipeline_request(ticker: str) -> PipelineRequest:
    cycle_ts = datetime.now(UTC).replace(second=0, microsecond=0)
    return PipelineRequest(ticker=ticker, cycle_ts=cycle_ts)


def create_app(  # noqa: C901, PLR0915
    settings: Settings | None = None,
    *,
    store: RunStore | None = None,
    orchestrator: PipelineOrchestrator | None = None,
) -> FastAPI:
    """Create one application with adapters fixed for its lifetime."""
    selected_settings = settings or Settings()
    configure_logging(debug=selected_settings.debug)
    if store is None:
        selected_orchestrator, selected_store = build_configured_orchestrator(selected_settings)
    else:
        selected_store = store
        selected_orchestrator = orchestrator or build_default_orchestrator(store=selected_store)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "web" / "templates")
    access = (
        ControlRoomAccess(selected_settings.control_room_token)
        if selected_settings.trading_enabled
        else None
    )
    review_runtime = (
        ReviewRuntime.build(
            str(selected_settings.database_url), build_market_data(selected_settings)
        )
        if selected_settings.database_mode is DatabaseMode.POSTGRES
        else None
    )
    live_run_runtime: LiveRunRuntime | None = None

    def _live_run_runtime() -> LiveRunRuntime:
        if live_run_runtime is None:
            msg = "live run runtime is not initialized"
            raise RuntimeError(msg)
        return live_run_runtime

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        nonlocal live_run_runtime
        async with anyio.create_task_group() as task_group:
            runtime = LiveRunRuntime(task_group, selected_orchestrator)
            live_run_runtime = runtime
            await selected_store.initialize()
            if review_runtime is not None:
                await review_runtime.initialize()
            try:
                yield
            finally:
                runtime.cancel()
                live_run_runtime = None
        if review_runtime is not None:
            await review_runtime.close()
        await selected_orchestrator.close()
        await selected_store.close()

    app = FastAPI(
        title=selected_settings.app_name,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "web" / "static"), name="static")
    if review_runtime is not None:
        app.include_router(build_review_router(review_runtime.processor, access=access))
    app.include_router(build_run_detail_router(selected_store))

    async def recent_control_room_runs() -> tuple[ControlRoomRun, ...]:
        runs = await selected_store.list_recent()
        active_runs = await selected_store.list_active()
        view_items: list[ControlRoomRun] = []
        for run in runs:
            attempts = await selected_store.list_attempts(run.run_id)
            view_items.append(control_room_run(run, attempts))
        for snapshot in active_runs:
            attempts = tuple(
                PersistedAttempt(
                    component=attempt.component,
                    attempt_no=attempt.attempt_no,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    finished_at=attempt.finished_at,
                    error_code=attempt.error_code,
                )
                for attempt in snapshot.attempts
            )
            view_items.append(control_room_run(snapshot.to_run(), attempts))
        return tuple(
            sorted(
                view_items,
                key=lambda item: (
                    item.cycle_ts,
                    item.status in {RunStatus.RUNNING, RunStatus.RETRYING},
                ),
                reverse=True,
            )
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, error: str | None = None) -> HTMLResponse:
        views = await recent_control_room_runs()
        durable_runs = await selected_store.list_recent()
        durable_account_id = next(
            (run.account_id for run in durable_runs if run.account_id is not None),
            None,
        )
        exposure_summary = (
            await selected_store.app_order_exposure_summary(
                account_id=durable_account_id,
                cap=selected_settings.max_app_order_exposure_usd,
            )
            if durable_account_id is not None
            else None
        )
        portfolio = simulated_portfolio_view(
            await selected_store.simulated_portfolio(
                selected_settings.simulated_account_opening_cash_usd
            )
        )
        completed_stages = views[0].progress if views else 0
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "runs": views,
                "latest": views[0] if views else None,
                "completed_stages": completed_stages,
                "exposure_summary": exposure_summary,
                "portfolio": portfolio,
                "dashboard_css": DASHBOARD_CSS,
                "settings": selected_settings,
                "control_room_access_required": access is not None,
                "form_error": error == "invalid_ticker",
            },
        )

    @app.post("/runs", response_class=RedirectResponse)
    async def run_from_form(
        request: Request,
        ticker: Annotated[str, Form()] = "NVDA",
        control_room_token: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if access is not None:
            access.require(request, control_room_token)
        try:
            payload = RunCreate(ticker=ticker)
        except ValidationError:
            return RedirectResponse(
                url="/?error=invalid_ticker", status_code=status.HTTP_303_SEE_OTHER
            )
        request_payload = _pipeline_request(payload.ticker)
        _ = _live_run_runtime().start(request_payload)
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/api/runs", response_model=PipelineRun, status_code=status.HTTP_201_CREATED)
    async def run_from_api(
        request: Request,
        payload: RunCreate,
        x_quantinue_control_token: Annotated[
            str | None, Header(alias="X-Quantinue-Control-Token")
        ] = None,
    ) -> PipelineRun:
        if access is not None:
            access.require(request, x_quantinue_control_token)
        return await selected_orchestrator.run(_pipeline_request(payload.ticker))

    @app.post(
        "/api/runs/async",
        response_model=AsyncRunStart,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_run_from_api(
        request: Request,
        payload: RunCreate,
        x_quantinue_control_token: Annotated[
            str | None, Header(alias="X-Quantinue-Control-Token")
        ] = None,
    ) -> AsyncRunStart:
        if access is not None:
            access.require(request, x_quantinue_control_token)
        request_payload = _pipeline_request(payload.ticker)
        accepted = _live_run_runtime().start(request_payload)
        return AsyncRunStart(
            accepted=accepted,
            ticker=request_payload.ticker,
            cycle_ts=request_payload.cycle_ts,
        )

    @app.get("/api/runs", response_model=list[ControlRoomRun])
    async def list_runs() -> list[ControlRoomRun]:
        return list(await recent_control_room_runs())

    @app.get("/api/portfolio", response_model=SimulatedPortfolioView)
    async def portfolio_observability() -> SimulatedPortfolioView:
        snapshot = await selected_store.simulated_portfolio(
            selected_settings.simulated_account_opening_cash_usd
        )
        return simulated_portfolio_view(snapshot)

    @app.get("/api/runs/{run_id}", response_model=ControlRoomRun)
    async def run_observability(run_id: str) -> ControlRoomRun:
        runs = await recent_control_room_runs()
        run = next((item for item in runs if str(item.run_id) == run_id), None)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return run

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            broker_mode=selected_settings.broker_mode.value,
            llm_mode=selected_settings.llm_mode.value,
        )

    return app


app = create_app()
