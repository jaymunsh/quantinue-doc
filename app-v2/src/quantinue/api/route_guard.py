"""Default-deny role zones for the two-sided web.

**미들웨어인 이유**: 라우트마다 가드를 붙이면 새 라우트를 추가하면서 하나
빠뜨린 것이 곧 구멍이고, 그 구멍은 아무 테스트도 실패시키지 않는다. 여기서는
반대다 — 모르는 경로는 막히고, 열려면 아래 allowlist에 이름을 적어야 한다.
W1-6의 라우트 감사 테스트가 강제하려는 것이 정확히 이 성질이다.

실제로 이 프로젝트에는 그 형태의 구멍이 이미 하나 있었다: 리뷰 POST는
``trading_enabled``일 때만 토큰을 검사해서, 거래를 꺼두면 인증 없이 열렸다.
"""

from __future__ import annotations

from hmac import compare_digest
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_303_SEE_OTHER, HTTP_401_UNAUTHORIZED, HTTP_404_NOT_FOUND

from quantinue.api.auth import session_user

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

    from quantinue.api.auth import UserReads

# 세션 없이 열리는 것들. /health는 살아있음 확인이라 로그인에 기대면 안 되고,
# /login과 /static은 로그인 화면 자신이 필요로 한다.
OPEN_PATHS = frozenset({"/login", "/logout", "/health"})
OPEN_PREFIXES = ("/static/",)

# 유저 구역. 나머지 전부는 관리자 구역이다 — "안전한 쪽이 기본"이라
# 새 관리자 화면을 추가할 때 아무것도 안 해도 막혀 있다.
USER_PREFIX = "/me"

# 헤더 **이름**이지 값이 아니다 — 토큰 자체는 설정에서 온다.
CONTROL_TOKEN_HEADER = "X-Quantinue-Control-Token"  # noqa: S105


def _is_open(path: str) -> bool:
    """Report whether the path needs no session at all."""
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def _wants_json(path: str) -> bool:
    """Report whether a refusal should be data rather than a login page."""
    return path.startswith("/api/")


async def _bootstrap_open(users: UserReads | None) -> bool:
    """Report whether zero accounts exist, which leaves the console open (W-D2).

    유저 원장을 물어볼 수 없는 저장소(메모리 모드)도 열림으로 친다. 계정을
    가질 수 없는 설치를 계정으로 잠그면 아무도 못 들어간다.
    """
    if users is None:
        return True
    counter = getattr(users, "count_users", None)
    if counter is None:
        return True
    return await counter() == 0


class RoleZoneGuard(BaseHTTPMiddleware):
    """Refuse every request that has not earned its zone."""

    def __init__(
        self,
        app: object,
        *,
        users: UserReads | None,
        control_token: str = "",
    ) -> None:
        """Capture the user ledger and the machine token this app accepts."""
        super().__init__(app)  # pyright: ignore[reportArgumentType]
        self._users = users
        self._control_token = control_token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Route the request into its zone or refuse it before the handler runs."""
        path = request.url.path
        if _is_open(path):
            return await call_next(request)
        if self._has_control_token(request):
            # 스케줄러·스크립트 경로. 사람 세션이 아니라 기계 자격증명이고,
            # 관리자와 같은 권한을 갖는다. 토큰이 설정돼 있지 않으면 이 문은
            # 아예 없다(빈 문자열과 비교해 통과하는 일이 없게).
            return await call_next(request)

        current = session_user(request)
        if current is None:
            if await _bootstrap_open(self._users):
                return await call_next(request)
            return self._refuse_anonymous(path)

        in_user_zone = path == USER_PREFIX or path.startswith(f"{USER_PREFIX}/")
        if not in_user_zone and not current.is_admin:
            # 403이 아니라 404다. "권한이 없다"는 거절은 그 경로가 존재한다는
            # 것을 확인해 준다.
            return self._not_found(path)
        return await call_next(request)

    def _has_control_token(self, request: Request) -> bool:
        """Check the machine token in constant time, treating unset as no door."""
        if not self._control_token:
            return False
        supplied = request.headers.get(CONTROL_TOKEN_HEADER)
        return supplied is not None and compare_digest(supplied, self._control_token)

    def _refuse_anonymous(self, path: str) -> Response:
        """Send a browser to the login screen and an API caller a status it can read."""
        if _wants_json(path):
            return JSONResponse(
                {"detail": "authentication required"}, status_code=HTTP_401_UNAUTHORIZED
            )
        return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)

    def _not_found(self, path: str) -> Response:
        """Answer an out-of-zone request as if the path did not exist."""
        if _wants_json(path):
            return JSONResponse({"detail": "not found"}, status_code=HTTP_404_NOT_FOUND)
        return PlainTextResponse("Not Found", status_code=HTTP_404_NOT_FOUND)
