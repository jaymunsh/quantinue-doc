"""Login, logout, and the promise that failures never reveal who exists."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from quantinue.api.passwords import hash_password
from quantinue.core.config import Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.users import UserRecord
from quantinue.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterable

_PASSWORD = "correct-horse-battery"


def _user(
    login_id: str,
    *,
    role: str = "user",
    password: str | None = _PASSWORD,
    is_active: bool = True,
) -> UserRecord:
    return UserRecord(
        user_id=abs(hash(login_id)) % 100_000,
        login_id=login_id,
        display_name=f"{login_id} 표시명",
        role=role,
        password_hash=None if password is None else hash_password(password),
        is_active=is_active,
    )


class _StubUsers:
    """Stand in for the Postgres user reads without a database."""

    def __init__(self, users: Iterable[UserRecord]) -> None:
        self._users = {user.login_id: user for user in users}

    async def count_users(self) -> int:
        return len(self._users)

    async def find_user_by_login(self, login_id: str) -> UserRecord | None:
        return self._users.get(login_id)


class _UserStore(InMemoryRunStore):
    """Attach a user ledger the way PostgresRunStore attaches its domain."""

    def __init__(self, users: Iterable[UserRecord]) -> None:
        super().__init__()
        self.domain = _StubUsers(users)


def _client(*users: UserRecord) -> TestClient:
    settings = Settings(app_name="Quantinue Test", session_secret="test-signing-key")  # type: ignore[arg-type]
    return TestClient(create_app(settings, store=_UserStore(users)), follow_redirects=False)


def test_login_page_offers_no_self_signup() -> None:
    """Self-signup is unsupported, and the screen says so instead of staying silent."""
    # Given
    with _client(_user("user1")) as client:
        # When
        response = client.get("/login")

    # Then
    assert response.status_code == 200
    assert "계정은 관리자가 발급합니다" in response.text
    assert "회원가입" not in response.text


def test_admin_lands_in_the_control_room() -> None:
    # Given
    with _client(_user("admin", role="admin")) as client:
        # When
        response = client.post("/login", data={"login_id": "admin", "password": _PASSWORD})

    # Then
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_user_lands_on_their_own_account() -> None:
    # Given
    with _client(_user("user1")) as client:
        # When
        response = client.post("/login", data={"login_id": "user1", "password": _PASSWORD})

    # Then
    assert response.status_code == 303
    assert response.headers["location"] == "/me"


def test_session_survives_the_next_request() -> None:
    # Given
    with _client(_user("user1")) as client:
        _ = client.post("/login", data={"login_id": "user1", "password": _PASSWORD})

        # When: an already-authenticated visitor asks for the login page
        response = client.get("/login")

    # Then: they are sent on rather than asked to sign in twice
    assert response.status_code == 303
    assert response.headers["location"] == "/me"


def test_logout_ends_the_session() -> None:
    # Given
    with _client(_user("user1")) as client:
        _ = client.post("/login", data={"login_id": "user1", "password": _PASSWORD})

        # When
        logout = client.post("/logout")
        after = client.get("/login")

    # Then
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"
    assert after.status_code == 200


def test_every_rejection_reads_exactly_the_same() -> None:
    """Wrong password, unknown id, disabled account, and no password are one answer.

    네 갈래가 한 글자라도 다르면 로그인 화면이 계정 목록을 알려주는 조회
    도구가 된다. 문구뿐 아니라 상태 코드와 본문 전체가 같아야 한다.
    """
    # Given
    users = (
        _user("known"),
        _user("disabled", is_active=False),
        _user("nopassword", password=None),
    )
    with _client(*users) as client:
        # When
        wrong_password = client.post("/login", data={"login_id": "known", "password": "nope"})
        unknown_id = client.post("/login", data={"login_id": "ghost", "password": "nope"})
        disabled = client.post("/login", data={"login_id": "disabled", "password": _PASSWORD})
        no_password = client.post("/login", data={"login_id": "nopassword", "password": "nope"})

    # Then
    answers = (wrong_password, unknown_id, disabled, no_password)
    assert {answer.status_code for answer in answers} == {401}
    assert len({answer.text for answer in answers}) == 1
    assert "아이디 또는 비밀번호가 올바르지 않습니다" in wrong_password.text


def test_rejection_never_echoes_the_submitted_id() -> None:
    """Echoing the id back turns a shared screen into a disclosure."""
    # Given
    with _client(_user("known")) as client:
        # When
        response = client.post(
            "/login", data={"login_id": "secret-person", "password": "nope"}
        )

    # Then
    assert "secret-person" not in response.text
