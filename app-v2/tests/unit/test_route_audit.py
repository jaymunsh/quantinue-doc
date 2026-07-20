"""Route audit — the completion criterion for Phase W1.

기획의 보안 서사는 "LLM 출력이 검증 없이 돈에 닿는 경로 0개"이고, 유저 화면이
조회 전용이라는 것이 그 서사의 일부다(phase1-decisions). 그 약속을 사람의
기억이 아니라 테스트가 지킨다.

**정적 검사와 동적 검사를 모두 한다.** 정적만 하면 "가드를 붙였다고 착각"할
수 있다 — 라우트 표에 이름이 있는 것과 요청이 실제로 막히는 것은 다른
사실이고, 이 프로젝트에서 실행으로만 잡힌 결함이 통산 20건이다.

⚠️ 라우트를 셀 때 ``app.routes``를 그냥 훑으면 안 된다. ``include_router``로
붙인 것은 ``APIRoute``로 평탄화되지 않고 감싼 객체로 남는다 — 처음 이 감사를
그렇게 썼다가 **유일한 쓰기 엔드포인트(리뷰 POST)를 하나도 안 보고 통과**할
뻔했다. 아래 ``_leaf_routes``가 재귀로 내려가는 이유다.

앱을 postgres 모양으로 세우는 것도 같은 이유다. 메모리 모드에서는 리뷰
라우터가 아예 안 붙어서, 감사가 지켜야 할 대상이 표에서 사라진다. 저장소만
스텁으로 주입하고 DB에는 붙지 않는다(가드가 핸들러보다 앞에 서므로 막힌
요청은 DB를 건드리지 않는다). 그래서 TestClient를 컨텍스트 매니저로 쓰지
않는다 — lifespan이 돌면 없는 DB에 연결하려 든다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from starlette.routing import Mount

from quantinue.api.passwords import hash_password
from quantinue.api.route_guard import OPEN_PATHS, OPEN_PREFIXES
from quantinue.core.config import Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.db.users import UserAccount, UserRecord
from quantinue.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI

_PASSWORD = "correct-horse-battery"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SESSION_ROUTES = frozenset({"/login", "/logout"})

# 세션 없이 열려도 되는 경로. 여기에 이름을 더하는 것이 곧 결정이고,
# 이 테스트가 그 결정을 눈에 띄게 만든다.
_EXPECTED_OPEN = frozenset({"/login", "/logout", "/health"})

# 연결되지 않는 주소. 가드가 막는 요청은 여기까지 오지 않는다.
_UNREACHABLE_DB = "postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5999/absent"

_USER = UserRecord(
    user_id=1,
    login_id="user1",
    display_name="데모 사용자 1",
    role="user",
    password_hash=hash_password(_PASSWORD),
    is_active=True,
)


class _StubDomain:
    """A user ledger that answers without a database."""

    async def count_users(self) -> int:
        return 1

    async def find_user_by_login(self, login_id: str) -> UserRecord | None:
        return _USER if login_id == _USER.login_id else None

    async def account_for_user(self, user_id: int) -> UserAccount | None:
        if user_id != _USER.user_id:
            return None
        return UserAccount(
            account_id=4,
            broker_account_id="DEMO-AGGRESSIVE-01",
            inv_type="aggressive",
            status="active",
            cash=Decimal("30734.62"),
            equity=Decimal("150000.00"),
        )


class _AuditStore(InMemoryRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.domain = _StubDomain()


def _app() -> FastAPI:
    """Build the production route shape without reaching a database."""
    settings = Settings(  # type: ignore[arg-type]
        app_name="Quantinue Test",
        session_secret="test-signing-key",
        database_mode="postgres",
        database_url=_UNREACHABLE_DB,
    )
    return create_app(settings, store=_AuditStore())


def _leaf_routes(routes: object) -> list[tuple[str, str]]:
    """Flatten every reachable route into (method, path), descending into routers."""
    found: list[tuple[str, str]] = []
    for route in routes:  # pyright: ignore[reportGeneralTypeIssues]
        nested = getattr(route, "original_router", None)
        if nested is not None:
            found.extend(_leaf_routes(nested.routes))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None:
            continue
        found.extend((method, path) for method in sorted(methods) if method != "HEAD")
    return found


def _is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def test_the_open_path_list_is_exactly_what_we_decided() -> None:
    """A path silently joining the allowlist is the whole failure mode this catches."""
    # Given / When / Then
    assert OPEN_PATHS == _EXPECTED_OPEN
    assert OPEN_PREFIXES == ("/static/",)


def test_the_audit_actually_sees_the_router_mounted_routes() -> None:
    """Guard against the flattening bug that made this audit inspect nothing."""
    # Given / When
    routes = _leaf_routes(_app().routes)

    # Then: the one write endpoint in the app is visible to the audit
    assert ("POST", "/api/reviews/{signal_id}/process") in routes
    assert ("POST", "/login") in routes


def test_every_open_route_was_declared_open() -> None:
    """Static half: a route may only skip the guard by being named in the allowlist."""
    # Given / When
    open_paths = {path for _, path in _leaf_routes(_app().routes) if _is_open(path)}

    # Then
    assert open_paths <= _EXPECTED_OPEN


def test_static_mount_is_the_only_mounted_subtree() -> None:
    """Anything else mounted would serve paths the guard was never shown."""
    # Given / When
    mounts = {route.path for route in _app().routes if isinstance(route, Mount)}

    # Then
    assert mounts == {"/static"}


def test_a_user_session_reaches_no_write_endpoint_at_all() -> None:
    """Dynamic half and the phase's completion criterion: user writes are zero."""
    # Given
    app = _app()
    write_routes = [
        (method, path)
        for method, path in _leaf_routes(app.routes)
        if method in _WRITE_METHODS and path not in _SESSION_ROUTES
    ]
    assert write_routes, "쓰기 라우트가 하나도 없으면 이 테스트는 아무것도 증명하지 않는다"

    # When
    client = TestClient(app, follow_redirects=False)
    login = client.post("/login", data={"login_id": _USER.login_id, "password": _PASSWORD})
    assert login.status_code == 303
    reached = [
        f"{method} {path}"
        for method, path in write_routes
        # 경로 매개변수는 아무 값으로나 채운다 — 가드는 핸들러보다 앞에 선다.
        if client.request(method, path.replace("{signal_id}", "1")).is_success
    ]

    # Then
    assert reached == []


def test_a_user_session_reaches_no_admin_read_either() -> None:
    """Read-only does not mean everyone's ledger — the admin zone stays invisible."""
    # Given
    app = _app()
    admin_reads = [
        path
        for method, path in _leaf_routes(app.routes)
        if method == "GET" and not _is_open(path) and not path.startswith("/me")
    ]
    assert admin_reads, "관리자 읽기 경로가 없으면 이 테스트는 아무것도 증명하지 않는다"

    # When
    client = TestClient(app, follow_redirects=False)
    _ = client.post("/login", data={"login_id": _USER.login_id, "password": _PASSWORD})
    reached = [path for path in admin_reads if client.get(path).is_success]

    # Then
    assert reached == []


def test_the_user_zone_is_actually_reachable() -> None:
    """A guard that refuses everything proves nothing — one door must open."""
    # Given / When
    client = TestClient(_app(), follow_redirects=False)
    _ = client.post("/login", data={"login_id": _USER.login_id, "password": _PASSWORD})
    response = client.get("/me")

    # Then
    assert response.status_code == 200
