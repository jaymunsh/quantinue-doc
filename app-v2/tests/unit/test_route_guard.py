"""Role zones, the bootstrap exception, and what a user may never reach."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from quantinue.api.passwords import hash_password
from quantinue.core.config import Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.users import UserAccount, UserRecord
from quantinue.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterable

_PASSWORD = "correct-horse-battery"


def _user(login_id: str, *, role: str = "user", user_id: int = 1) -> UserRecord:
    return UserRecord(
        user_id=user_id,
        login_id=login_id,
        display_name=f"{login_id} 표시명",
        role=role,
        password_hash=hash_password(_PASSWORD),
        is_active=True,
    )


class _StubDomain:
    """A user ledger with no job ledger — enough to exercise the guard."""

    def __init__(
        self, users: Iterable[UserRecord], accounts: dict[int, UserAccount] | None = None
    ) -> None:
        self._users = {user.login_id: user for user in users}
        self._accounts = accounts or {}

    async def count_users(self) -> int:
        return len(self._users)

    async def find_user_by_login(self, login_id: str) -> UserRecord | None:
        return self._users.get(login_id)

    async def account_for_user(self, user_id: int) -> UserAccount | None:
        return self._accounts.get(user_id)

    # 관제실은 잡 원장도 읽는다. 이 테스트가 묻는 것은 가드이지 화면 내용이
    # 아니므로 전부 비워 둔다 — 빈 슬롯도 관제실의 정상 상태다.
    async def latest_job_slot(self) -> None:
        return None

    async def recent_job_slots(self, *, limit: int) -> tuple[()]:
        _ = limit
        return ()

    async def job_runs(self, slot_date: object) -> tuple[()]:
        _ = slot_date
        return ()

    async def order_plans(self, trade_date: object) -> tuple[()]:
        _ = trade_date
        return ()

    async def account_equity_series(self, *, days: int) -> tuple[()]:
        _ = days
        return ()

    async def judgements(self, trade_date: object) -> tuple[()]:
        _ = trade_date
        return ()


class _GuardStore(InMemoryRunStore):
    def __init__(self, domain: _StubDomain | None) -> None:
        super().__init__()
        if domain is not None:
            self.domain = domain


def _client(domain: _StubDomain | None) -> TestClient:
    settings = Settings(app_name="Quantinue Test", session_secret="test-signing-key")  # type: ignore[arg-type]
    return TestClient(create_app(settings, store=_GuardStore(domain)), follow_redirects=False)


def _login(client: TestClient, login_id: str) -> None:
    response = client.post("/login", data={"login_id": login_id, "password": _PASSWORD})
    assert response.status_code == 303


def test_control_room_opens_while_no_account_exists() -> None:
    """The bootstrap exception (W-D2): an installation with no users is not locked out.

    관리자 계정이 생기는 순간 잠긴다. 그 전에 잠그면 첫 설치가 자기 화면을
    열 방법이 없다.
    """
    # Given / When
    with _client(_StubDomain(users=())) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200


def test_control_room_locks_once_an_account_exists() -> None:
    # Given
    with _client(_StubDomain(users=(_user("admin", role="admin"),))) as client:
        # When
        response = client.get("/")

    # Then
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_reaches_the_control_room() -> None:
    # Given
    with _client(_StubDomain(users=(_user("admin", role="admin"),))) as client:
        _login(client, "admin")

        # When
        response = client.get("/")

    # Then
    assert response.status_code == 200


def test_user_cannot_learn_that_the_admin_zone_exists() -> None:
    """404, not 403 — a refusal that names the resource still confirms it."""
    # Given
    with _client(_StubDomain(users=(_user("user1"),))) as client:
        _login(client, "user1")

        # When
        page = client.get("/")
        api = client.get("/api/pipeline/today")
        portfolio = client.get("/api/portfolio")

    # Then
    assert {page.status_code, api.status_code, portfolio.status_code} == {404}


def test_user_sees_their_own_account() -> None:
    # Given
    account = UserAccount(
        account_id=7, broker_account_id="DEMO-AGGRESSIVE-01", inv_type="aggressive", status="active"
    )
    domain = _StubDomain(users=(_user("user1", user_id=1),), accounts={1: account})
    with _client(domain) as client:
        _login(client, "user1")

        # When
        response = client.get("/me")

    # Then
    assert response.status_code == 200
    assert "DEMO-AGGRESSIVE-01" in response.text


def test_account_screen_invents_nothing_for_an_owner_less_login() -> None:
    """An admin has no account, so /me has nothing true to show."""
    # Given
    with _client(_StubDomain(users=(_user("admin", role="admin"),))) as client:
        _login(client, "admin")

        # When
        response = client.get("/me")

    # Then
    assert response.status_code == 404


def test_health_stays_reachable_without_a_session() -> None:
    """Liveness must not depend on being able to sign in."""
    # Given / When
    with _client(_StubDomain(users=(_user("admin", role="admin"),))) as client:
        response = client.get("/health")

    # Then
    assert response.status_code == 200


def test_unauthenticated_api_is_refused_without_a_redirect() -> None:
    """An API caller gets an answer, not a login page it cannot render."""
    # Given / When
    with _client(_StubDomain(users=(_user("admin", role="admin"),))) as client:
        response = client.get("/api/pipeline/today")

    # Then
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
