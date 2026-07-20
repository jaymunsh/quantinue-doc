"""The sign-in and sign-out routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from quantinue.api.auth import (
    UserReads,
    forget_user,
    remember_user,
    session_user,
)
from quantinue.api.passwords import verify_password

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

# 네 갈래(없는 아이디·틀린 비밀번호·정지된 계정·비밀번호 미설정)가 모두
# 이 한 문장으로 끝난다. 갈래마다 다른 말을 하면 로그인 화면이 계정 목록을
# 알려주는 조회 도구가 된다.
REJECTION_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."

LOGIN_PATH = "/login"


def build_auth_router(users: UserReads | None, templates: Jinja2Templates) -> APIRouter:
    """Bind the user ledger for HTTP sign-in, or refuse every attempt without one."""
    router = APIRouter(tags=["auth"])

    def _render_login(request: Request, *, status_code: int) -> HTMLResponse:
        # 실패 응답에 제출된 아이디를 되돌려 적지 않는다 — 공용 화면에서
        # 그 한 줄이 곧 "이 아이디를 시도했다"는 공개다.
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            status_code=status_code,
            context={
                "error": REJECTION_MESSAGE if status_code != status.HTTP_200_OK else None,
            },
        )

    @router.get(LOGIN_PATH, response_class=HTMLResponse, response_model=None)
    async def login_form(request: Request) -> HTMLResponse | RedirectResponse:
        current = session_user(request)
        if current is not None:
            return RedirectResponse(
                current.landing_path, status_code=status.HTTP_303_SEE_OTHER
            )
        return _render_login(request, status_code=status.HTTP_200_OK)

    @router.post(LOGIN_PATH, response_class=HTMLResponse, response_model=None)
    async def login_submit(
        request: Request,
        login_id: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> HTMLResponse | RedirectResponse:
        record = None if users is None else await users.find_user_by_login(login_id)
        # 해시 검증은 계정이 없을 때도 돈다(verify_password가 더미로 같은 비용을
        # 치른다). 없는 아이디만 즉시 돌아가면 응답 시간이 계정 존재를 알린다.
        matched = verify_password(record.password_hash if record else None, password)
        if record is None or not record.is_active or not matched:
            return _render_login(request, status_code=status.HTTP_401_UNAUTHORIZED)
        remember_user(request, record)
        landing = "/" if record.role == "admin" else "/me"
        return RedirectResponse(landing, status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        forget_user(request)
        return RedirectResponse(LOGIN_PATH, status_code=status.HTTP_303_SEE_OTHER)

    return router
