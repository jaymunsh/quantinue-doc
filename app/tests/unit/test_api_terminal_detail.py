from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from typing_extensions import override

from quantinue.api.presentation import (
    control_room_run,
    source_reference_view,
    terminal_run_detail_view,
)
from quantinue.api.schemas import ControlRoomRun, TerminalRunDetailView
from quantinue.core.contracts import PipelineRun, RunId, RunStatus
from quantinue.core.terminal_detail import (
    CollectionFact,
    CriticDetail,
    RoleDetail,
    StrategyDetail,
    TerminalRunDetail,
)
from quantinue.db.contracts import PersistedAttempt
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app


class DetailRunStore(InMemoryRunStore):
    def __init__(self, run: PipelineRun) -> None:
        super().__init__()
        self._run = run

    @override
    async def list_recent(self, limit: int = 20) -> tuple[PipelineRun, ...]:
        return (self._run,)[:limit]


def test_role_detail_status_tracks_latest_runtime_attempt() -> None:
    now = datetime(2026, 7, 13, tzinfo=UTC)
    run = PipelineRun(
        run_id=RunId("detail-runtime-status"),
        ticker="NVDA",
        cycle_ts=now,
        status=RunStatus.FAILED,
        stages=(),
        detail=TerminalRunDetail(roles=(RoleDetail(component="05", title="공시 분석"),)),
    )
    view = control_room_run(
        run,
        (
            PersistedAttempt(
                component="05",
                attempt_no=1,
                status="failed",
                started_at=now,
                error_code="PROVIDER_FAILURE",
            ),
        ),
    )

    assert view.detail.roles[0].status == "failed"


def test_terminal_detail_view_plain_texts_legacy_news_markup() -> None:
    # Given
    detail = TerminalRunDetail(
        news=CollectionFact(
            title="Tesla update",
            summary=(
                '<a href="https://news.example/story">BofA Maintains Tesla</a>'
                '&nbsp;<font color="#6f6f6f">富途牛牛</font>'
            ),
        ),
        roles=(
            RoleDetail(
                component="06",
                title="뉴스 분석",
                facts=(("요약", '<a href="https://news.example/story">Tesla story</a>'),),
            ),
        ),
    )

    # When
    view = terminal_run_detail_view(detail)

    # Then
    assert view.news.summary == "BofA Maintains Tesla 富途牛牛"
    assert view.roles[0].facts == (("요약", "Tesla story"),)


@pytest.mark.parametrize(
    ("reference", "label"),
    [
        ("sec://filing/0001", "sec://filing/0001"),
        ("fixture://news/NVDA", "fixture://news/NVDA"),
        ("https://example.invalid/fixture-news", "https://example.invalid/fixture-news"),
        ("javascript:alert(1)", "non-web reference"),
        ("data:text/plain,provider-payload", "non-web reference"),
        ("https://user:password@example.com/private", "https://example.com/private"),
        ("http://[::1", "invalid reference"),
        ("https://example.com/report\nLINE_MARKER", "invalid reference"),
        ("https://example.com/\x00NUL_MARKER\tTAB_MARKER", "invalid reference"),
        (" https://example.com/LEADING_MARKER", "invalid reference"),
    ],
)
def test_source_reference_view_never_links_non_public_reference(reference: str, label: str) -> None:
    # Given: a parameterized source reference and expected readable label

    # When
    view = source_reference_view(reference)

    # Then
    assert view.href is None
    assert view.label == label


def test_terminal_detail_api_projects_only_safe_clickable_references() -> None:
    # Given
    run = PipelineRun(
        run_id=RunId("detail-safe"),
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        stages=(),
        detail=TerminalRunDetail(
            disclosure=CollectionFact(
                title="10-Q",
                summary="Revenue improved",
                source="SEC EDGAR",
                reference="https://www.sec.gov/ixviewer/doc/action?filing=10q",
                score=0.8,
            ),
            news=CollectionFact(
                title="Fixture news",
                summary="Readable but not a browser link",
                source="Fixture RSS",
                reference="fixture://news/NVDA",
                score=0.6,
            ),
            strategy=StrategyDetail(
                proposal="buy",
                rationale="bounded rationale",
                gate="approved",
                blockers=("none",),
                conviction=0.82,
            ),
            critic=CriticDetail(verdict="pass", rationale="bounded critic", layer="risk"),
        ),
    )
    app = create_app(store=DetailRunStore(run))

    # When
    with TestClient(app) as client:
        response = client.get("/api/runs/detail-safe/detail")
        observability = client.get("/api/runs/detail-safe")

    # Then
    assert response.status_code == 200
    payload = TerminalRunDetailView.model_validate_json(response.content)
    assert payload.disclosure.reference.href == "https://www.sec.gov/ixviewer/doc/action"
    assert payload.news.reference.label == "fixture://news/NVDA"
    assert payload.news.reference.href is None
    assert payload.strategy.conviction == 0.82
    assert observability.status_code == 200
    full_view = ControlRoomRun.model_validate_json(observability.content)
    assert full_view.detail == payload


def test_source_reference_view_keeps_valid_public_url_clickable() -> None:
    # Given
    reference = "https://www.sec.gov/Archives/edgar/data/1045810/report.htm?token=ignored"

    # When
    view = source_reference_view(reference)

    # Then
    assert view.label == "https://www.sec.gov/Archives/edgar/data/1045810/report.htm"
    assert view.href == view.label


def test_terminal_detail_api_never_turns_hostile_or_credential_url_into_link() -> None:
    # Given
    run = PipelineRun(
        run_id=RunId("detail-hostile"),
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        stages=(),
        detail=TerminalRunDetail(
            disclosure=CollectionFact(
                title="source",
                summary="safe text",
                source="source",
                reference="javascript:alert(1)",
            ),
            news=CollectionFact(
                title="source",
                summary="safe text",
                source="source",
                reference="https://user:password@example.com/private\nCONTROL_RAW_MARKER",
            ),
        ),
    )
    app = create_app(store=DetailRunStore(run))

    # When
    with TestClient(app) as client:
        response = client.get("/api/runs/detail-hostile/detail")

    # Then
    assert response.status_code == 200
    payload = TerminalRunDetailView.model_validate_json(response.content)
    assert payload.disclosure.reference.href is None
    assert payload.news.reference.href is None
    assert "password" not in response.text


def test_terminal_detail_list_api_projects_only_redacted_run_views() -> None:
    # Given
    run = PipelineRun(
        run_id=RunId("detail-list-safe"),
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        stages=(),
        detail=TerminalRunDetail(
            disclosure=CollectionFact(
                title="source",
                summary="safe text",
                source="source",
                reference="https://user:password@example.com/private\nCONTROL_RAW_MARKER",
            ),
            news=CollectionFact(
                title="source",
                summary="safe text",
                source="source",
                reference="data:text/plain,NON_WEB_RAW_MARKER",
            ),
            strategy=StrategyDetail(rationale="bounded strategy"),
            critic=CriticDetail(rationale="bounded critic"),
        ),
    )
    app = create_app(store=DetailRunStore(run))

    # When
    with TestClient(app) as client:
        response = client.get("/api/runs")

    # Then
    assert response.status_code == 200
    payload = TypeAdapter(list[ControlRoomRun]).validate_json(response.content)
    assert payload[0].run_id == run.run_id
    assert payload[0].detail.disclosure.reference.label == "invalid reference"
    assert payload[0].detail.news.reference.label == "non-web reference"
    assert "password" not in response.text
    assert "CONTROL_RAW_MARKER" not in response.text
    assert "NON_WEB_RAW_MARKER" not in response.text


@pytest.mark.parametrize(
    ("case", "reference", "marker"),
    [
        ("newline", "https://example.com/report\nLINE_MARKER", "LINE_MARKER"),
        ("nul_tab", "https://example.com/\x00NUL_MARKER\tTAB_MARKER", "NUL_MARKER"),
        ("leading_space", " https://example.com/LEADING_MARKER", "LEADING_MARKER"),
    ],
)
def test_terminal_detail_api_redacts_control_character_reference(
    case: str, reference: str, marker: str
) -> None:
    # Given
    run = PipelineRun(
        run_id=RunId(f"control-{case}"),
        ticker="NVDA",
        cycle_ts=datetime(2026, 7, 13, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        stages=(),
        detail=TerminalRunDetail(disclosure=CollectionFact(reference=reference)),
    )

    # When
    with TestClient(create_app(store=DetailRunStore(run))) as client:
        response = client.get(f"/api/runs/{run.run_id}/detail")

    # Then
    assert response.status_code == 200
    payload = TerminalRunDetailView.model_validate_json(response.content)
    assert payload.disclosure.reference.label == "invalid reference"
    assert payload.disclosure.reference.href is None
    assert marker not in response.text
