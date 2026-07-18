from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from quantinue.api.access import ControlRoomAccess
from quantinue.core.config import BrokerMode, Settings
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


def test_paper_enabled_run_routes_require_a_same_origin_control_room_token() -> None:
    # Given
    settings = Settings(
        broker_mode=BrokerMode.ALPACA,
        trading_enabled=True,
        alpaca_api_key=SecretStr("test-key"),
        alpaca_secret_key=SecretStr("test-secret"),
        control_room_token=SecretStr("test-control-room-token"),
    )
    app = create_app(settings)

    # When / Then
    with TestClient(app) as client:
        missing = client.post("/api/runs", json={"ticker": "NVDA"})
        cross_origin = client.post(
            "/api/runs",
            json={"ticker": "NVDA"},
            headers={
                "Origin": "https://untrusted.example",
                "X-Quantinue-Control-Token": "test-control-room-token",
            },
        )
        accepted_form = client.post(
            "/runs",
            data={"ticker": "invalid", "control_room_token": "test-control-room-token"},
        )

    assert missing.status_code == 403
    assert cross_origin.status_code == 403
    assert accepted_form.status_code == 200
    assert "티커 형식을 확인하세요" in accepted_form.text
