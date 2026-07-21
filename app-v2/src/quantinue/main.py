"""FastAPI application and server-rendered control room."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from quantinue.api.access import ControlRoomAccess
from quantinue.api.account_roster import AccountRosterView, account_roster_view
from quantinue.api.admin_accounts import build_admin_accounts_router
from quantinue.api.auth import session_user
from quantinue.api.login_routes import build_auth_router
from quantinue.api.my_account import MyAccountView, my_account_view
from quantinue.api.ops_log import OpsLogView, build_ops_log
from quantinue.api.pipeline_day import (
    DEFAULT_CURVE_DAYS,
    build_pipeline_day,
    empty_pipeline_day,
)
from quantinue.api.pipeline_presentation import PipelineDayView, sparkline_points
from quantinue.api.review_runtime import ReviewRuntime
from quantinue.api.reviews import build_review_router
from quantinue.api.route_guard import RoleZoneGuard
from quantinue.api.schedule import ScheduleView, build_schedule
from quantinue.api.schemas import HealthResponse
from quantinue.api.sessions import resolve_session_secret
from quantinue.core.config import DatabaseMode, Settings
from quantinue.core.logging import configure_logging
from quantinue.core.market_calendar import NEW_YORK
from quantinue.db.store import build_run_store
from quantinue.market_data.factory import build_market_data
from quantinue.orchestration.job_factory import (
    JobSources,
    build_budgeted_analyzer,
    build_job_runner,
    build_watch_runner,
)
from quantinue.orchestration.policy import load_mvp2_config
from quantinue.web.timefmt import register_filters

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager

    from quantinue.db.contracts import RunStore
    from quantinue.db.users import UserAccount
    from quantinue.orchestration.job_runner import JobRunner
    from quantinue.orchestration.watch_runner import WatchRunner

# 타임라인에 몇 건을 보여줄지. 판단 문턱이 아니라 표시용 창이라 config가
# 아니라 여기 산다 — 어떤 매매 결정에도 들어가지 않는다.
DEFAULT_TIMELINE_ENTRIES = 50

PACKAGE_DIR = Path(__file__).parent
DASHBOARD_CSS = (PACKAGE_DIR / "web" / "static" / "dashboard.css").read_text(encoding="utf-8")


def _lifespan_factory(
    *,
    store: RunStore,
    review_runtime: ReviewRuntime | None,
    market_data: object,
    job_runner: JobRunner | None,
    watch_runner: WatchRunner | None,
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
            if watch_runner is not None:
                task_group.start_soon(watch_runner.run_forever)
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


def _mount_reviews(
    app: FastAPI, review_runtime: ReviewRuntime | None, access: ControlRoomAccess | None
) -> None:
    """Mount the T+5 review router when a durable store backs it."""
    if review_runtime is not None:
        app.include_router(build_review_router(review_runtime.processor, access=access))


async def _owned_account(reads: object | None, current: object | None) -> UserAccount | None:
    """Look up the caller's own account, if the store and the session both allow it."""
    reader = getattr(reads, "account_for_user", None)
    if current is None or reader is None:
        return None
    return await reader(current.user_id)


def _landing_target(current: object | None) -> str:
    """Pick the screen this role owns. 부트스트랩(세션 없음)은 관제실로 보낸다."""
    return "/me" if current is not None and not current.is_admin else "/admin"


def _mount_schedule(  # noqa: PLR0913 - 협력자 나열이지 분기 아님
    app: FastAPI,
    templates: Jinja2Templates,
    reads: object | None,
    settings: Settings,
    config: object,
    job_runner: JobRunner | None,
) -> None:
    """Mount the operating-basis page: when jobs run and on whose clock."""

    @app.get("/admin/schedule", response_class=HTMLResponse)
    async def schedule(request: Request) -> HTMLResponse:
        # 잡 이름은 러너에게 묻는다. 화면이 자기 목록을 들면 등록이 갈리는
        # 설치(자격증명 없음)에서 화면과 실행이 다른 이야기를 한다.
        names = () if job_runner is None else tuple(job.name for job in job_runner.jobs)
        view = (
            ScheduleView(
                slot_date=datetime.now(UTC).astimezone(NEW_YORK).date(),
                is_trading_day=False,
                jobs_enabled=False,
                tick_seconds=60,
            )
            if reads is None
            else await build_schedule(
                job_names=names,
                config=config.jobs,
                ledger=reads,
                now=datetime.now(UTC),
            )
        )
        return templates.TemplateResponse(
            request=request,
            name="schedule.html",
            context={
                "schedule": view,
                "settings": settings,
                "current_user": session_user(request),
            },
        )


def _mount_ops_log(
    app: FastAPI,
    templates: Jinja2Templates,
    reads: object | None,
    settings: Settings,
) -> None:
    """Mount the day-by-day operations log page."""

    @app.get("/admin/logs", response_class=HTMLResponse)
    async def ops_log(request: Request, page: int = 1) -> HTMLResponse:
        log = OpsLogView() if reads is None else await build_ops_log(reads, page=page)
        return templates.TemplateResponse(
            request=request,
            name="ops_log.html",
            context={
                "log": log,
                "settings": settings,
                "current_user": session_user(request),
            },
        )


async def _account_roster(reads: object | None) -> AccountRosterView:
    """Read every account, or report none when the store has no ledger.

    메모리 스토어에는 계좌 원장이 없다. 그때 빈 총람은 "계좌가 없다"로
    정직하게 읽힌다 — 없는 것 대신 유령 계좌 하나를 그리던 것이 §1-1이다.
    """
    reader = getattr(reads, "account_overviews", None)
    if reader is None:
        return AccountRosterView()
    return account_roster_view(await reader())


async def _my_account(reads: object | None, account: UserAccount) -> MyAccountView:
    """Assemble the owner's page from reads already scoped to their account.

    ``account``은 소유권 질의(``account_for_user``)가 돌려준 것이다. 뒤따르는
    두 읽기가 그 ``account_id``만 보므로 소유 판정은 여전히 한 곳에 있다.
    잡 원장이 없는 스토어(메모리)에서는 보유·곡선이 비고, 그건 "아직 거래가
    없다"로 정직하게 읽힌다.
    """
    holdings_reader = getattr(reads, "account_holdings", None)
    curve_reader = getattr(reads, "account_curve", None)
    timeline_reader = getattr(reads, "account_timeline", None)
    holdings = () if holdings_reader is None else await holdings_reader(account.account_id)
    curve = (
        ()
        if curve_reader is None
        else await curve_reader(account.account_id, days=DEFAULT_CURVE_DAYS)
    )
    timeline = (
        ()
        if timeline_reader is None
        else await timeline_reader(account.account_id, limit=DEFAULT_TIMELINE_ENTRIES)
    )
    macro_reader = getattr(reads, "latest_macro_observation", None)
    macro = None if macro_reader is None else await macro_reader()
    return my_account_view(account, holdings, curve, timeline, macro)


def create_app(settings: Settings | None = None, *, store: RunStore | None = None) -> FastAPI:
    """Create one application with adapters fixed for its lifetime."""
    selected_settings = settings or Settings()
    configure_logging(debug=selected_settings.debug)
    mvp2_config = load_mvp2_config(PACKAGE_DIR.parent.parent / "config" / "pipeline.yaml")
    selected_store = store if store is not None else build_run_store(selected_settings)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "web" / "templates")
    # 공통 셸(base.html)이 인라인하는 값이라 화면마다 컨텍스트로 나르지 않는다.
    # 빠뜨린 화면 하나가 스타일 없이 렌더되는 것을 막는다.
    templates.env.globals["dashboard_css"] = DASHBOARD_CSS
    # 사람이 시계를 읽는 자리는 전부 KST다. 원장(UTC)·슬롯(뉴욕 날짜)은 그대로.
    register_filters(templates.env)
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
            # 판단 콜은 예산 원장을 통과한다. 원장은 도메인 저장소가 들고
            # 있으므로(메모리 스토어에는 없다) 여기서 넘긴다 — 없으면 감싸지
            # 않고, 그건 잡도 안 도는 설치라 예산을 지킬 대상 자체가 없다.
            analyzer=build_budgeted_analyzer(
                selected_settings,
                mvp2_config,
                ledger=getattr(selected_store, "domain", None),
            ),
        ),
    )
    watch_runner = build_watch_runner(
        selected_settings,
        mvp2_config,
        store=selected_store,
    )

    app = FastAPI(
        title=selected_settings.app_name,
        lifespan=_lifespan_factory(
            store=selected_store,
            review_runtime=review_runtime,
            market_data=market_data,
            job_runner=job_runner if mvp2_config.jobs.enabled else None,
            watch_runner=watch_runner if mvp2_config.watch.enabled else None,
        ),
    )
    # 잡 원장은 RunStore 프로토콜 밖에 산다(도메인 저장소 소유). 메모리
    # 스토어에는 아예 없으므로, 없으면 빈 관제실을 보여준다 — 잡을 아직 안
    # 켠 설치도 정상 상태이고 그때 화면이 500으로 죽으면 안 된다.
    # 같은 저장소가 유저 원장도 들고 있다(tb_user). 없으면 로그인할 계정이
    # 없다는 뜻이고, 그건 부트스트랩 상태다(W-D2).
    control_room_reads = getattr(selected_store, "domain", None)

    # ⚠️ 미들웨어는 **나중에 더한 것이 바깥**이다. 세션이 가드보다 바깥에 있어야
    # 가드가 request.session을 읽을 수 있으므로 가드를 먼저 더한다. 순서를
    # 뒤집으면 가드가 늘 "로그인 안 됨"으로 보고 전부 막힌다.
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    app.add_middleware(
        RoleZoneGuard,
        users=control_room_reads,
        control_token=selected_settings.control_room_token.get_secret_value(),
    )
    # 세션 쿠키는 서명만 되고 암호화되지 않는다(auth.SESSION_KEY 주석). https_only는
    # 켜지 않는다 — 로컬 http로 띄우는 것이 이 단계의 정상 운용이고, 켜면 쿠키가
    # 조용히 사라져 "로그인이 안 된다"로 보인다. R1(페이퍼 전환) 때 재검토.
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolve_session_secret(selected_settings),
        session_cookie="quantinue_session",
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "web" / "static"), name="static")
    _mount_reviews(app, review_runtime, access)
    app.include_router(build_auth_router(control_room_reads, templates))
    app.include_router(build_admin_accounts_router(control_room_reads, templates))

    @app.get("/me", response_class=HTMLResponse)
    async def my_account(request: Request) -> HTMLResponse:
        current = session_user(request)
        account = await _owned_account(control_room_reads, current)
        if account is None:
            # 계좌 없는 로그인(관리자·부트스트랩)에 빈 계좌를 그리지 않는다.
            # 없는 것을 0으로 그리면 화면이 원장에 없는 사실을 지어낸다.
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return templates.TemplateResponse(
            request=request,
            name="me.html",
            context={
                "view": await _my_account(control_room_reads, account),
                "current_user": current,
            },
        )

    async def pipeline_day(slot: date | None = None) -> PipelineDayView:
        if control_room_reads is None:
            return empty_pipeline_day()
        return await build_pipeline_day(
            control_room_reads,
            slot_date=slot,
            llm_limit_usd=mvp2_config.budget.daily_llm_usd,
        )

    @app.get("/")
    async def role_landing(request: Request) -> RedirectResponse:
        """Send each signed-in role to the screen that belongs to it.

        최상위 주소를 관제실로 두면 유저가 여기서 404를 맞는다. 화면이 아니라
        갈림길로 만들어 두면 역할이 늘어도 이 한 곳만 고치면 된다.
        """
        return RedirectResponse(
            _landing_target(session_user(request)), status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def control_room(request: Request, slot: date | None = None) -> HTMLResponse:
        day = await pipeline_day(slot)
        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "day": day,
                "roster": await _account_roster(control_room_reads),
                "sparkline": sparkline_points,
                "settings": selected_settings,
                "current_user": session_user(request),
            },
        )

    _mount_schedule(
        app, templates, control_room_reads, selected_settings, mvp2_config, job_runner
    )
    _mount_ops_log(app, templates, control_room_reads, selected_settings)

    @app.get("/api/accounts", response_model=AccountRosterView)
    async def accounts_observability() -> AccountRosterView:
        return await _account_roster(control_room_reads)

    @app.get("/api/pipeline/today", response_model=PipelineDayView)
    async def pipeline_today(slot: date | None = None) -> PipelineDayView:
        return await pipeline_day(slot)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            broker_mode=selected_settings.broker_mode.value,
            llm_mode=selected_settings.llm_mode.value,
        )

    return app


app = create_app()
