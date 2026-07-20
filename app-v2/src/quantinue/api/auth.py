"""Session identity for the admin/user web split."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from starlette.requests import Request

    from quantinue.db.users import UserRecord

# 세션 딕셔너리 안의 키. 쿠키는 **서명만 되고 암호화되지는 않는다** —
# user_id·role이 base64로 들여다보인다(변조는 불가). 그래서 여기에는 조회에
# 필요한 식별자와 표시명만 담고, 비밀은 무엇도 담지 않는다.
SESSION_KEY = "quantinue_user"

ADMIN_ROLE = "admin"
USER_ROLE = "user"


@dataclass(frozen=True, slots=True)
class SessionUser:
    """Who the current request belongs to, as recovered from the signed cookie."""

    user_id: int
    login_id: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        """Report admin membership without spreading the role literal around."""
        return self.role == ADMIN_ROLE

    @property
    def landing_path(self) -> str:
        """Point each role at the only screen it is allowed to open first."""
        return "/" if self.is_admin else "/me"


class UserReads(Protocol):
    """The two questions the web layer asks about accounts."""

    async def count_users(self) -> int:
        """Return how many accounts exist, deciding the bootstrap exception."""
        ...

    async def find_user_by_login(self, login_id: str) -> UserRecord | None:
        """Return one sign-in candidate, active or not, or None."""
        ...


def remember_user(request: Request, user: UserRecord) -> None:
    """Write the signed session for a successfully authenticated visitor."""
    request.session[SESSION_KEY] = {
        "user_id": user.user_id,
        "login_id": user.login_id,
        "display_name": user.display_name,
        "role": user.role,
    }


def forget_user(request: Request) -> None:
    """Drop the session so the cookie stops identifying anyone."""
    _ = request.session.pop(SESSION_KEY, None)


def session_user(request: Request) -> SessionUser | None:
    """Recover the signed identity, treating any malformed payload as absent.

    쿠키가 오래돼 모양이 바뀌었을 수 있다. 그때 예외를 던지면 500이 나가고
    사용자는 로그아웃조차 못 한다 — 모르는 모양은 "로그인 안 됨"으로 읽는다.
    """
    raw = request.session.get(SESSION_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return SessionUser(
            user_id=int(raw["user_id"]),
            login_id=str(raw["login_id"]),
            display_name=str(raw["display_name"]),
            role=str(raw["role"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
