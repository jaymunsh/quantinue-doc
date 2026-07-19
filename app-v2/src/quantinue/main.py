"""FastAPI application and server-rendered control room."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date  # noqa: TC003 - FastAPI가 런타임에 쿼리 타입을 해석한다
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from quantinue.api.access import ControlRoomAccess
from quantinue.api.pipeline_day import build_pipeline_day, empty_pipeline_day
from quantinue.api.pipeline_presentation import PipelineDayView, sparkline_points
from quantinue.api.portfolio_view import simulated_portfolio_view
from quantinue.api.review_runtime import ReviewRuntime
from quantinue.api.reviews import build_review_router
from quantinue.api.schemas import HealthResponse, SimulatedPortfolioView
from quantinue.core.config import DatabaseMode, Settings
from quantinue.core.logging import configure_logging
from quantinue.db.store import build_run_store
from quantinue.llm.provider import build_llm_analyzer
from quantinue.market_data.factory import build_market_data
from quantinue.orchestration.job_factory import JobSources, build_job_runner
from quantinue.orchestration.policy import load_mvp2_config

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager

    from quantinue.db.contracts import RunStore
    from quantinue.orchestration.job_runner import JobRunner

PACKAGE_DIR = Path(__file__).parent
DASHBOARD_CSS = (PACKAGE_DIR / "web" / "static" / "dashboard.css").read_text(encoding="utf-8")


def _lifespan_factory(
    *,
    store: RunStore,
    review_runtime: ReviewRuntime | None,
    market_data: object,
    job_runner: JobRunner | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Own every application-lifetime resource, including the ones jobs collect through.

    수집 어댑터를 여기서 닫는 이유는 구 러너가 없어졌기 때문이다 — 예전에는
    오케스트레이터가 자기가 만든 자원만 소유했고, 잡이 쓰는 어댑터는 아무도
    닫지 않았다.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        async with anyio.create_task_group() as task_group:
            await store.initialize()
            if review_runtime is not None:
                await review_runtime.initialize()
            if job_runner is not None:
                task_group.start_soon(job_runner.run_forever)
            try:
                yield
            finally:
                # run_forever는 스스로 끝나지 않는다. 취소하지 않으면 task
                # group이 자식을 기다리느라 앱 종료가 영원히 안 끝난다 — 구
                # 코드에서는 LiveRunRuntime.cancel()이 전체 스코프를 취소해
                # 줬는데 그 역할이 러너와 함께 조용히 사라졌었다. jobs.enabled가
                # false인 동안은 자식이 없어 드러나지 않던 결함이다.
                task_group.cancel_scope.cancel()
        if review_runtime is not None:
            await review_runtime.close()
        closer = getattr(market_data, "aclose", None)
        if closer is not None:
            await closer()
        await store.close()

    return lifespan


def create_app(settings: Settings | None = None, *, store: RunStore | None = None) -> FastAPI:
    """Create one application with adapters fixed for its lifetime."""
    selected_settings = settings or Settings()
    configure_logging(debug=selected_settings.debug)
    mvp2_config = load_mvp2_config(PACKAGE_DIR.parent.parent / "config" / "pipeline.yaml")
    selected_store = store if store is not None else build_run_store(selected_settings)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "web" / "templates")
    # 화면은 읽기 전용이 됐지만 리뷰 처리(POST)는 여전히 상태를 바꾼다 —
    # 토큰 게이트가 지킬 대상은 이제 그쪽이다.
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
    # 어댑터를 한 번만 만든다. 예전에는 유니버스용·매크로용으로 두 번 만들어
    # 하나는 아무도 닫지 않았다 — 러너가 자기 것만 소유했기 때문이다.
    market_data = build_market_data(selected_settings)
    job_runner = build_job_runner(
        selected_settings,
        mvp2_config,
        store=selected_store,
        sources=JobSources(
            market_data=market_data,
            # 같은 어댑터가 유니버스와 매크로를 모두 구현한다 — 필드가 갈라져
            # 있는 것은 테스트 조립의 타입 정직성 때문이다(JobSources 주석).
            macro=market_data,
            analyzer=build_llm_analyzer(selected_settings),
        ),
    )

    app = FastAPI(
        title=selected_settings.app_name,
        lifespan=_lifespan_factory(
            store=selected_store,
            review_runtime=review_runtime,
            market_data=market_data,
            job_runner=job_runner if mvp2_config.jobs.enabled else None,
        ),
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "web" / "static"), name="static")
    if review_runtime is not None:
        app.include_router(build_review_router(review_runtime.processor, access=access))

    # 잡 원장은 RunStore 프로토콜 밖에 산다(도메인 저장소 소유). 메모리
    # 스토어에는 아예 없으므로, 없으면 빈 관제실을 보여준다 — 잡을 아직 안
    # 켠 설치도 정상 상태이고 그때 화면이 500으로 죽으면 안 된다.
    control_room_reads = getattr(selected_store, "domain", None)

    async def pipeline_day(slot: date | None = None) -> PipelineDayView:
        if control_room_reads is None:
            return empty_pipeline_day()
        return await build_pipeline_day(control_room_reads, slot_date=slot)

    @app.get("/", response_class=HTMLResponse)
    async def control_room(request: Request, slot: date | None = None) -> HTMLResponse:
        day = await pipeline_day(slot)
        portfolio = simulated_portfolio_view(
            await selected_store.simulated_portfolio(
                selected_settings.simulated_account_opening_cash_usd
            )
        )
        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "day": day,
                "portfolio": portfolio,
                "sparkline": sparkline_points,
                "dashboard_css": DASHBOARD_CSS,
                "settings": selected_settings,
            },
        )

    @app.get("/api/pipeline/today", response_model=PipelineDayView)
    async def pipeline_today(slot: date | None = None) -> PipelineDayView:
        return await pipeline_day(slot)

    @app.get("/api/portfolio", response_model=SimulatedPortfolioView)
    async def portfolio_observability() -> SimulatedPortfolioView:
        snapshot = await selected_store.simulated_portfolio(
            selected_settings.simulated_account_opening_cash_usd
        )
        return simulated_portfolio_view(snapshot)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            broker_mode=selected_settings.broker_mode.value,
            llm_mode=selected_settings.llm_mode.value,
        )

    return app


app = create_app()
