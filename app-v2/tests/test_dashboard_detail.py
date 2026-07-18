from datetime import UTC, datetime

from fastapi.testclient import TestClient
from typing_extensions import override

from quantinue.api.presentation import ROLE_DESCRIPTIONS, source_reference_view
from quantinue.api.schemas import ControlRoomRun
from quantinue.core.contracts import PipelineRun, RunId, RunStatus
from quantinue.core.terminal_detail import (
    CollectionFact,
    CriticDetail,
    NewsSelectionDetail,
    NewsSelectionDetailItem,
    RoleDetail,
    StrategyDetail,
    TerminalRunDetail,
)
from quantinue.db.contracts import PersistedAttempt
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app
from quantinue.market_data.models import NewsMatchStatus


def test_role07_description_advertises_only_buy_and_hold_actions() -> None:
    # Given / When
    description = ROLE_DESCRIPTIONS["07"]

    # Then
    assert "매수·보유" in description
    assert "매도" not in description


class DetailRunStore(InMemoryRunStore):
    def __init__(self, run: PipelineRun, attempt: PersistedAttempt) -> None:
        super().__init__()
        self._run = run
        self._attempt = attempt

    @override
    async def list_recent(self, limit: int = 20) -> tuple[PipelineRun, ...]:
        return (self._run,)[:limit]

    @override
    async def list_attempts(self, run_id: RunId) -> tuple[PersistedAttempt, ...]:
        return (self._attempt,) if run_id == self._run.run_id else ()


def test_dashboard_renders_safe_collection_to_critic_brief() -> None:
    # Given
    started = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    run = PipelineRun(
        run_id=RunId("detail-brief"),
        ticker="NVDA",
        cycle_ts=started,
        status=RunStatus.COMPLETED,
        stages=(),
        detail=TerminalRunDetail(
            disclosure=CollectionFact(
                title="10-Q filing",
                summary="Revenue increased year over year.",
                source="SEC EDGAR",
                reference="https://www.sec.gov/Archives/edgar/data/1",
                score=0.82,
            ),
            news=CollectionFact(
                title="Market update",
                summary="Demand remained steady.",
                source="Wire",
                reference="fixture://news/NVDA",
                score=0.71,
            ),
            strategy=StrategyDetail(
                proposal="buy",
                rationale="The setup meets the quality threshold.",
                gate="passed",
                blockers=("position limit",),
                conviction=0.78,
            ),
            critic=CriticDetail(
                verdict="pass",
                rationale="No hard risk gate triggered.",
                layer="risk_review",
            ),
        ),
    )
    app = create_app(store=DetailRunStore(run, _attempt("completed", started)))

    # When
    with TestClient(app) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert "수집부터 비평까지" in response.text
    assert "10-Q filing" in response.text
    assert "Market update" in response.text
    assert "전략가 제안" in response.text
    assert "비평가 판정" in response.text
    assert "82.0%" in response.text
    assert "71.0%" in response.text
    assert "역할별 상세 처리 데이터" in response.text
    assert "상세 원장이 없는 이전 실행입니다" in response.text
    assert 'href="https://www.sec.gov/Archives/edgar/data/1"' in response.text
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer"' in response.text
    assert "fixture://news/NVDA" in response.text
    assert 'href="fixture://news/NVDA"' not in response.text


def test_source_reference_keeps_long_safe_url_and_disables_digest_link() -> None:
    # Given
    long_safe = f"https://news.example.test/articles/{'a' * 800}"
    digest = "long-reference:sha256:" + "f" * 64

    # When
    long_view = source_reference_view(long_safe)
    digest_view = source_reference_view(digest)
    short_view = source_reference_view("https://news.example.test/short?q=hidden#fragment")

    # Then
    assert long_view.label == long_safe
    assert long_view.href == long_safe
    assert digest_view.label == digest
    assert digest_view.href is None
    assert short_view.label == "https://news.example.test/short"
    assert short_view.href == "https://news.example.test/short"


def test_dashboard_marks_legacy_detail_as_unavailable() -> None:
    # Given
    started = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    run = PipelineRun(
        run_id=RunId("legacy-detail"),
        ticker="NVDA",
        cycle_ts=started,
        status=RunStatus.FAILED,
        stages=(),
    )
    app = create_app(store=DetailRunStore(run, _attempt("failed", started)))

    # When
    with TestClient(app) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert "표시 가능한 수집·판단 정보가 없습니다" in response.text
    assert "failed 상태" in response.text


def test_role06_renders_exact_structured_selection_without_url_secrets() -> None:
    started = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    selected_url = "https://user:pass@news.example.test/nvda?q=secret#token"
    relevant_url = "https://news.example.test/supply?key=hidden"
    excluded_url = "https://news.example.test/other#fragment"
    selection_detail = NewsSelectionDetail(
        items=(
            NewsSelectionDetailItem(
                status=NewsMatchStatus.SELECTED,
                is_representative=True,
                score=90,
                reasons=("ticker_title", "company_title"),
                title="NVIDIA · 실적, 발표",
                published_at="2026-07-13 07:00:00+00:00",
                reference=selected_url,
            ),
            NewsSelectionDetailItem(
                status=NewsMatchStatus.RELEVANT,
                is_representative=False,
                score=55,
                reasons=("ticker_snippet · delimiter",),
                title="공급망 · 업데이트",
                published_at="2026-07-13 06:00:00+00:00",
                reference=relevant_url,
            ),
            NewsSelectionDetailItem(
                status=NewsMatchStatus.EXCLUDED,
                is_representative=False,
                score=0,
                reasons=("below_minimum_score",),
                title="무관한 뉴스",
                published_at="2026-07-13 05:00:00+00:00",
                reference=excluded_url,
            ),
        )
    )
    detail = TerminalRunDetail(
        roles=(
            RoleDetail(
                component="06",
                title="뉴스 분석",
                status="completed",
                summary="대표 뉴스 분석 완료",
                news_selection=selection_detail,
            ),
        )
    )
    run = PipelineRun(
        run_id=RunId("news-selection-detail"),
        ticker="NVDA",
        cycle_ts=started,
        status=RunStatus.COMPLETED,
        stages=(),
        detail=detail,
    )
    app = create_app(store=DetailRunStore(run, _attempt("completed", started)))

    with TestClient(app) as client:
        dashboard = client.get("/")
        api = client.get("/api/runs/news-selection-detail")

    assert dashboard.status_code == 200
    assert "전체 수집" in dashboard.text
    assert "관련 뉴스" in dashboard.text
    assert "대표 분석" in dashboard.text
    assert "NVIDIA · 실적, 발표" in dashboard.text
    assert "무관한 뉴스" in dashboard.text
    assert "user:pass" not in dashboard.text
    assert "q=secret" not in dashboard.text
    assert "key=hidden" not in dashboard.text
    assert "fragment" not in dashboard.text
    selection = ControlRoomRun.model_validate_json(api.content).detail.roles[0].news_selection
    assert selection is not None
    assert selection.fetched_count == 3
    assert selection.relevant_count == 2
    assert selection.excluded_count == 1
    assert selection.representative_count == 1
    assert len(selection.items) == 3
    assert selection.items[0].title == "NVIDIA · 실적, 발표"
    assert selection.items[1].reasons == ("ticker_snippet · delimiter",)


def test_role06_legacy_representative_explains_unscored_selection_without_fixture_link() -> None:
    # Given
    started = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    detail = TerminalRunDetail(
        roles=(
            RoleDetail(
                component="06",
                title="뉴스 분석",
                status="completed",
                news_selection=NewsSelectionDetail(
                    items=(
                        NewsSelectionDetailItem(
                            status=NewsMatchStatus.SELECTED,
                            is_representative=True,
                            score=0,
                            relevance_evaluated=False,
                            representative_label="분석에 사용된 대표 소스",
                            representative_explanation=(
                                "기존 실행은 관련성 선별 점수를 기록하지 않았으며, "
                                "실제 모델 분석에 사용된 소스를 대표 항목으로 표시합니다."
                            ),
                            title="Deterministic fixture news",
                            published_at="2026-07-13 07:00:00+00:00",
                            reference="https://example.invalid/fixture-news",
                        ),
                    )
                ),
            ),
        )
    )
    run = PipelineRun(
        run_id=RunId("legacy-news-selection"),
        ticker="NVDA",
        cycle_ts=started,
        status=RunStatus.COMPLETED,
        stages=(),
        detail=detail,
    )

    # When
    with TestClient(
        create_app(store=DetailRunStore(run, _attempt("completed", started)))
    ) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert "관련성 점수 미산정" in response.text
    assert "선정 근거 없음" not in response.text
    assert "기존 실행은 관련성 선별 점수를 기록하지 않았으며" in response.text
    assert 'href="https://example.invalid/fixture-news"' not in response.text
    assert "https://example.invalid/fixture-news" in response.text


def test_role06_keeps_every_structured_row_when_display_values_are_malformed() -> None:
    started = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    detail = TerminalRunDetail(
        roles=(
            RoleDetail(
                component="06",
                title="뉴스 분석",
                status="completed",
                news_selection=NewsSelectionDetail(
                    items=(
                        NewsSelectionDetailItem(
                            status=NewsMatchStatus.EXCLUDED,
                            is_representative=False,
                            score=0,
                            title="첫째 · 발행 · 출처",
                            published_at="not-a-date · 그대로",
                            reference="not a url",
                        ),
                        NewsSelectionDetailItem(
                            status=NewsMatchStatus.RELEVANT,
                            is_representative=False,
                            score=1,
                            reasons=("근거 · 쉼표, 포함",),
                            title="둘째",
                            published_at="",
                            reference="https://news.example.test/two?q=secret#fragment",
                        ),
                    )
                ),
            ),
        )
    )
    run = PipelineRun(
        run_id=RunId("news-edge-detail"),
        ticker="NVDA",
        cycle_ts=started,
        status=RunStatus.COMPLETED,
        stages=(),
        detail=detail,
    )
    app = create_app(store=DetailRunStore(run, _attempt("completed", started)))

    with TestClient(app) as client:
        response = client.get("/api/runs/news-edge-detail")

    selection = ControlRoomRun.model_validate_json(response.content).detail.roles[0].news_selection
    assert selection is not None
    assert selection.fetched_count == 2
    assert len(selection.items) == 2
    assert selection.items[0].title == "첫째 · 발행 · 출처"
    assert selection.items[0].reference.label == "invalid reference"
    assert selection.items[1].reference.label == "https://news.example.test/two"


def _attempt(status: str, started_at: datetime) -> PersistedAttempt:
    return PersistedAttempt(component="01", attempt_no=1, status=status, started_at=started_at)
