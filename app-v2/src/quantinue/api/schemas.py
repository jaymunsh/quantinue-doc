"""FastAPI response schemas for the health and portfolio surfaces.

구 관제실의 런 뷰 20여 종(ControlRoomRun·StageView·RoleDetailView…)이 살던
파일이다. 그 화면과 러너가 함께 죽어서, 남은 것은 지금 라우트가 실제로
돌려주는 것뿐이다 — 잡 기반 화면의 뷰는 ``api/pipeline_presentation``에 산다.
"""


from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Safe runtime mode summary."""

    model_config = ConfigDict(frozen=True)

    status: str
    broker_mode: str
    llm_mode: str


