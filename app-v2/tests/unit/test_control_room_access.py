from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from quantinue.api.access import ControlRoomAccess
from quantinue.core.config import BrokerMode, Settings
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app


def test_control_room_access_rejects_missing_or_cross_origin_token() -> None:
    # Given
    app = FastAPI()
    access = ControlRoomAccess(SecretStr("test-control-room-token"))

    @app.post("/protected", dependencies=[Depends(access)])
    async def protected() -> None:
        return None

    # When / Then
    with TestClient(app) as client:
        missing = client.post("/protected")
        cross_origin = client.post(
            "/protected",
            headers={
                "Origin": "https://untrusted.example",
                "X-Quantinue-Control-Token": "test-control-room-token",
            },
        )
        accepted = client.post(
            "/protected",
            headers={"X-Quantinue-Control-Token": "test-control-room-token"},
        )

    assert missing.status_code == 403
    assert cross_origin.status_code == 403
    assert accepted.status_code == 200


def test_the_review_route_is_the_one_that_still_needs_the_token() -> None:
    """구 러너 삭제로 실행 트리거가 사라졌다 — 남은 변경 경로는 리뷰 처리뿐이다.

    이 테스트는 삭제된 ``POST /api/runs``·``POST /runs`` 게이트 테스트의 대체다.
    화면이 읽기 전용이 됐다고 토큰 게이트가 필요 없어지는 것이 아니라, 지킬
    대상이 옮겨간 것이다.
    """
    # Given
    settings = Settings.model_validate(
        {
            "broker_mode": BrokerMode.MOCK,
            "trading_enabled": True,
            "control_room_token": "test-control-room-token",
            "database_mode": "postgres",
            "database_url": "postgresql+asyncpg://test:test@127.0.0.1:55400/test",
        }
    )
    app = create_app(settings, store=InMemoryRunStore())

    # When / Then — lifespan은 실제 DB를 요구하므로 라우터만 검사한다
    client = TestClient(app)
    missing = client.post("/api/reviews/1/process")
    cross_origin = client.post(
        "/api/reviews/1/process",
        headers={
            "Origin": "https://untrusted.example",
            "X-Quantinue-Control-Token": "test-control-room-token",
        },
    )

    assert missing.status_code == 403
    assert cross_origin.status_code == 403
