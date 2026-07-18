from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from quantinue.api.presentation import (
    LEGACY_REPRESENTATIVE_EXPLANATION,
    terminal_run_detail_view,
)
from quantinue.core.contracts import (
    DisclosureSourceRecord,
    NewsSourceRecord,
    PipelineContext,
    PipelineRequest,
)
from quantinue.core.terminal_detail import RoleDetail
from quantinue.db.store import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.market_data.models import NewsMatchReason, NewsMatchStatus
from quantinue.orchestration.factory import build_default_orchestrator
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput
from quantinue.roles.role_08_critic.service import Critic

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


def test_role_detail_preserves_unbounded_items() -> None:
    # Given
    long_item = "x" * 4_096
    every_item = tuple(long_item for _ in range(2_001))

    # When
    detail = RoleDetail(component="02", title="기술 분석", items=every_item)

    # Then
    assert detail.items == every_item


def test_collection_roles_show_every_fetched_source_and_mark_representative() -> None:
    # Given
    filings = tuple(
        DisclosureSourceRecord(
            filing_no=f"filing-{index}",
            title=f"Filing {index}",
            form_type="8-K",
            filed_at=NOW,
            event_type="other",
            source_ref=f"https://example.test/filing/{index}?access_token=redacted#fragment",
            summary=f"Summary {index}",
        )
        for index in range(2)
    )
    news = tuple(
        NewsSourceRecord(
            news_key=f"news-{index}",
            title=f"News {index}",
            url=f"https://user:pass@example.test/news/{index}?token=redacted#fragment",
            source="rss",
            published_at=NOW,
            summary=f"Summary {index}",
            selection_status=(NewsMatchStatus.FETCHED if index == 0 else NewsMatchStatus.EXCLUDED),
            relevance_score=90 if index == 0 else 0,
            relevance_reasons=(
                (NewsMatchReason.TICKER_TITLE,)
                if index == 0
                else (NewsMatchReason.BELOW_MINIMUM_SCORE,)
            ),
        )
        for index in range(3)
    )
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=NOW),
        disclosure_source=filings[0],
        disclosure_sources=filings,
        news_source=news[0],
        news_sources=news,
    )

    # When
    detail = context.to_run().detail

    # Then
    assert len(detail.roles[4].items) == 2
    assert detail.roles[5].items == ()
    assert detail.roles[4].items[0].startswith("대표 분석")
    selection = detail.roles[5].news_selection
    assert selection is not None
    assert len(selection.items) == 3
    assert selection.items[0].is_representative is True
    assert selection.items[0].status is NewsMatchStatus.SELECTED
    assert selection.items[0].score == 90
    assert selection.items[0].reasons == (NewsMatchReason.TICKER_TITLE,)
    assert all("access_token" not in item for item in detail.roles[4].items)
    assert all(
        "user:pass" not in item.reference and "token=" not in item.reference
        for item in selection.items
    )


def test_news_detail_preserves_long_safe_reference_without_secrets() -> None:
    # Given
    long_path = "a" * 800
    news = NewsSourceRecord(
        news_key="news-long-reference",
        title="NVDA provider redirect",
        url=f"https://news.example.test/articles/{long_path}?token=redacted#fragment",
        source="rss",
        published_at=NOW,
        summary="Provider redirect fixture.",
        selection_status=NewsMatchStatus.SELECTED,
        relevance_score=90,
        relevance_reasons=(NewsMatchReason.TICKER_TITLE,),
    )
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=NOW),
        news_source=news,
        news_sources=(news,),
    )

    # When
    selection = context.to_run().detail.roles[5].news_selection

    # Then
    assert selection is not None
    assert selection.items[0].reference == f"https://news.example.test/articles/{long_path}"
    assert "token" not in selection.items[0].reference


def test_news_detail_digests_over_contract_references_without_collisions() -> None:
    # Given
    path_a = "a" * 5_000
    path_b = "b" * 5_000
    news = tuple(
        NewsSourceRecord(
            news_key=f"news-over-contract-{index}",
            title="NVDA provider redirect",
            url=f"https://user:secret@news.example.test/articles/{path}?token=hidden#fragment",
            source="rss",
            published_at=NOW,
            summary="Provider redirect fixture.",
            selection_status=(NewsMatchStatus.SELECTED if index == 0 else NewsMatchStatus.RELEVANT),
            relevance_score=90,
            relevance_reasons=(NewsMatchReason.TICKER_TITLE,),
        )
        for index, path in enumerate((path_a, path_b))
    )
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=NOW),
        news_source=news[0],
        news_sources=news,
    )

    # When
    selection = context.to_run().detail.roles[5].news_selection

    # Then
    assert selection is not None
    references = tuple(item.reference for item in selection.items)
    assert all(reference.startswith("long-reference:sha256:") for reference in references)
    assert references[0] != references[1]
    assert all("secret" not in reference for reference in references)
    assert all("token" not in reference and "fragment" not in reference for reference in references)


class _CollectedDetailRole:
    component: ClassVar[str] = "01"
    name: ClassVar[str] = "collect detail"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        collected = replace(
            context,
            disclosure_score=0.78,
            disclosure_source=DisclosureSourceRecord(
                filing_no="filing-1",
                title="Collected filing",
                form_type="8-K",
                filed_at=NOW,
                event_type="other",
                source_ref="sec://filing/filing-1",
                summary="A collected summary.",
            ),
        )
        return collected.add_stage(self.component, self.name, "done")


class _FailingAfterDetailRole:
    component: ClassVar[str] = "02"
    name: ClassVar[str] = "fail after detail"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        del context
        msg = "fixture failure"
        raise RuntimeError(msg)


@pytest.mark.anyio
async def test_pipeline_run_retains_safe_collection_strategy_and_critic_detail() -> None:
    # Given
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)

    # When
    run = await build_default_orchestrator().run(request)

    # Then
    assert run.detail.disclosure.title == "Deterministic fixture filing"
    assert run.detail.disclosure.summary == "Quarterly results exceeded expectations."
    assert run.detail.disclosure.source == "sec-edgar"
    assert run.detail.disclosure.reference == "sec://filing/fixture-filing"
    assert run.detail.disclosure.score == 0.78
    assert run.detail.news.title == "Deterministic fixture news"
    assert run.detail.news.summary == "AI accelerator orders expanded."
    assert run.detail.news.source == "reuters.com"
    assert run.detail.news.reference == "https://example.invalid/fixture-news"
    assert run.detail.news.score == 0.74
    fixture_selection = run.detail.roles[5].news_selection
    assert fixture_selection is not None
    assert len(fixture_selection.items) == 1
    assert fixture_selection.items[0].is_representative is True
    assert fixture_selection.items[0].status is NewsMatchStatus.SELECTED
    assert (
        fixture_selection.items[0].relevance_evaluated,
        fixture_selection.items[0].representative_label,
        fixture_selection.items[0].representative_explanation,
    ) == (
        False,
        "분석에 사용된 대표 소스",
        LEGACY_REPRESENTATIVE_EXPLANATION,
    )
    fixture_view = terminal_run_detail_view(run.detail).roles[5].news_selection
    assert fixture_view is not None
    assert (
        fixture_view.fetched_count,
        fixture_view.relevant_count,
        fixture_view.excluded_count,
        fixture_view.representative_count,
        fixture_view.items[0].relevance_evaluated,
        fixture_view.items[0].representative_label,
    ) == (1, 1, 0, 1, False, "분석에 사용된 대표 소스")
    assert fixture_view.fetched_count == fixture_view.relevant_count + fixture_view.excluded_count
    assert run.detail.strategy.proposal == "buy"
    assert run.detail.strategy.rationale == "기술·공시·뉴스 합의"
    assert run.detail.strategy.gate == "passed"
    assert run.detail.strategy.blockers == ()
    assert run.detail.strategy.conviction == 0.775
    assert run.detail.critic.verdict == "pass"
    assert run.detail.critic.rationale == "강한 반증과 하드 블로커 없음"
    assert run.detail.critic.layer == "gate"
    assert tuple(role.component for role in run.detail.roles) == tuple(
        f"{component:02d}" for component in range(1, 12)
    )
    assert run.detail.roles[0].facts == (("종목 수", "1"),)
    assert run.detail.roles[0].items[0].startswith("NVDA · NVIDIA Corporation")
    assert run.detail.roles[1].items[0].startswith("NVDA · 종가")
    assert "MACD" in run.detail.roles[1].items[0]
    assert "MA50" in run.detail.roles[1].items[0]
    assert run.detail.roles[2].items[0].startswith("#1 NVDA")
    assert ("국면", "neutral") in run.detail.roles[3].facts
    assert ("중요도", "0.8") in run.detail.roles[4].facts
    assert ("감성", "0.78") in run.detail.roles[4].facts
    assert ("뉴스 수", "2") in run.detail.roles[5].facts
    assert ("출처 신뢰", "0.9") in run.detail.roles[5].facts
    assert ("카테고리", None) not in run.detail.roles[7].facts
    assert ("계획", "planned") in run.detail.roles[8].facts
    assert any(label == "신호 ID" for label, _ in run.detail.roles[8].facts)
    assert ("상태", "filled") in run.detail.roles[9].facts
    assert "로컬 모의 체결" in run.detail.roles[9].summary
    assert any("T+1" in item and "T+5" in item for item in run.detail.roles[10].items)


@pytest.mark.anyio
async def test_critic_hard_block_retains_typed_rejection_detail() -> None:
    # Given
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=NOW),
        side="buy",
        conviction=0.8,
        last_price=100.0,
        macro_regime="risk_off",
    )

    # When
    updated = await Critic(DeterministicAnalyzer()).execute(context)

    # Then
    assert updated.critic_approved is False
    assert updated.critic_verdict is not None
    assert updated.critic_verdict.decision == "reject"
    assert updated.critic_verdict.objection == "risk-off regime"
    assert updated.critic_verdict.decided_layer == "hard_rule"
    detail = updated.to_run().detail
    assert detail.critic.verdict == "reject"
    assert detail.critic.rationale == "risk-off regime"
    assert detail.critic.layer == "hard_rule"


def test_legacy_context_keeps_empty_terminal_detail_placeholders() -> None:
    # Given
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=NOW))

    # When
    run = context.to_run()

    # Then
    assert run.detail.disclosure.title == ""
    assert run.detail.news.title == ""
    assert run.detail.strategy.proposal == ""
    assert run.detail.critic.verdict == ""


def test_terminal_detail_bounds_untrusted_strategy_rationale() -> None:
    # Given
    source = StrategyInput.fixture()
    strategy = StrategyOutput.from_model(source, conviction=0.8, summary="x" * 1_001)
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=NOW),
        strategy_output=strategy,
    )

    # When
    detail = context.to_run().detail

    # Then
    assert detail.strategy.rationale == "x" * 1_000


@pytest.mark.anyio
async def test_failed_terminal_run_retains_collected_detail() -> None:
    # Given
    request = PipelineRequest(ticker="NVDA", cycle_ts=NOW)
    store = InMemoryRunStore()

    # When
    with pytest.raises(RuntimeError, match="fixture failure"):
        _ = await PipelineOrchestrator(
            (_CollectedDetailRole(), _FailingAfterDetailRole()), store
        ).run(request)

    # Then
    failed = (await store.list_recent())[0]
    assert failed.detail.disclosure.title == "Collected filing"
    assert failed.detail.disclosure.score == 0.78
