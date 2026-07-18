"""Project role facts into bounded terminal detail."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, Protocol, assert_never
from urllib.parse import urlsplit, urlunsplit

from quantinue.core.terminal_detail import (
    DISPLAY_REFERENCE_MAX_LENGTH,
    CollectionFact,
    CriticDetail,
    NewsSelectionDetail,
    NewsSelectionDetailItem,
    RoleDetail,
    StrategyDetail,
    TerminalRunDetail,
)
from quantinue.market_data.models import NewsMatchStatus

LEGACY_EXPLANATION_PREFIX: Final = "기존 실행은 관련성 선별 점수를 기록하지 않았으며,"
LEGACY_EXPLANATION_SUFFIX: Final = "실제 모델 분석에 사용된 소스를 대표 항목으로 표시합니다."
LEGACY_REPRESENTATIVE_EXPLANATION: Final = (
    f"{LEGACY_EXPLANATION_PREFIX} {LEGACY_EXPLANATION_SUFFIX}"
)

if TYPE_CHECKING:
    from quantinue.core.terminal_run_types import OrderResult, ReviewResult
    from quantinue.llm.provider import AnalysisResult
    from quantinue.market_data.models import NewsMatchReason
    from quantinue.roles.role_01_universe_screener.contracts import UniverseScreenerOutput
    from quantinue.roles.role_02_technical_analysis.contracts import TechnicalAnalysisOutput
    from quantinue.roles.role_03_daily_screener.contracts import DailyScreenerOutput
    from quantinue.roles.role_04_macro_analysis.contracts import MacroAnalysisOutput
    from quantinue.roles.role_05_disclosure_analysis.contracts import DisclosureSignal
    from quantinue.roles.role_06_news_analysis.contracts import NewsSignal


class _StageDetail(Protocol):
    @property
    def component(self) -> str: ...

    @property
    def summary(self) -> str: ...


class _DisclosureDetailSource(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def source_ref(self) -> str: ...

    @property
    def form_type(self) -> str: ...

    @property
    def event_type(self) -> str: ...

    @property
    def filed_at(self) -> object: ...

    @property
    def confidence(self) -> float: ...


class _NewsDetailSource(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def published_at(self) -> object: ...

    @property
    def confidence(self) -> float: ...

    @property
    def selection_status(self) -> NewsMatchStatus: ...

    @property
    def relevance_score(self) -> int: ...

    @property
    def relevance_reasons(self) -> tuple[NewsMatchReason, ...]: ...


class _StrategyDetailOutput(Protocol):
    @property
    def side(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def gate_passed(self) -> bool: ...

    @property
    def blockers(self) -> tuple[str, ...]: ...

    @property
    def conviction(self) -> float: ...


class _CriticDetailOutput(Protocol):
    @property
    def decision(self) -> str: ...

    @property
    def objection(self) -> str | None: ...

    @property
    def decided_layer(self) -> str: ...

    @property
    def category(self) -> str | None: ...

    @property
    def confidence(self) -> float: ...

    @property
    def source(self) -> str: ...

    @property
    def skipped_rules(self) -> tuple[str, ...]: ...


class _DetailContext(Protocol):
    @property
    def stages(self) -> tuple[_StageDetail, ...]: ...

    @property
    def universe_output(self) -> UniverseScreenerOutput | None: ...

    @property
    def technical_output(self) -> TechnicalAnalysisOutput | None: ...

    @property
    def daily_screener_output(self) -> DailyScreenerOutput | None: ...

    @property
    def macro_output(self) -> MacroAnalysisOutput | None: ...
    @property
    def disclosure_source(self) -> _DisclosureDetailSource | None: ...

    @property
    def news_source(self) -> _NewsDetailSource | None: ...

    @property
    def disclosure_sources(self) -> tuple[_DisclosureDetailSource, ...]: ...

    @property
    def news_sources(self) -> tuple[_NewsDetailSource, ...]: ...

    @property
    def disclosure_output(self) -> DisclosureSignal | None: ...

    @property
    def news_output(self) -> NewsSignal | None: ...

    @property
    def disclosure_analysis(self) -> AnalysisResult | None: ...

    @property
    def news_analysis(self) -> AnalysisResult | None: ...

    @property
    def signal_id(self) -> int | None: ...

    @property
    def account_id(self) -> int | None: ...

    @property
    def disclosure_score(self) -> float | None: ...

    @property
    def news_score(self) -> float | None: ...

    @property
    def strategy_output(self) -> _StrategyDetailOutput | None: ...

    @property
    def critic_verdict(self) -> _CriticDetailOutput | None: ...

    @property
    def risk_decision(self) -> str | None: ...

    @property
    def risk_skipped_reason(self) -> str | None: ...

    @property
    def risk_entry_price(self) -> float | None: ...

    @property
    def quantity(self) -> int | None: ...

    @property
    def stop_loss(self) -> float | None: ...

    @property
    def take_profit(self) -> float | None: ...

    @property
    def order(self) -> OrderResult | None: ...

    @property
    def review(self) -> ReviewResult | None: ...


def terminal_detail_from_context(context: _DetailContext) -> TerminalRunDetail:
    """Build display-safe detail without parsing localized stage summaries."""
    disclosure = context.disclosure_source
    news = context.news_source
    strategy = context.strategy_output
    critic = context.critic_verdict
    return TerminalRunDetail(
        disclosure=CollectionFact(
            title=disclosure.title[:200] if disclosure is not None else "",
            summary=disclosure.summary[:1_000] if disclosure is not None else "",
            source=disclosure.source[:120] if disclosure is not None else "",
            reference=disclosure.source_ref[:512] if disclosure is not None else "",
            score=context.disclosure_score,
        ),
        news=CollectionFact(
            title=news.title[:200] if news is not None else "",
            summary=news.summary[:1_000] if news is not None else "",
            source=news.source[:120] if news is not None else "",
            reference=news.url[:512] if news is not None else "",
            score=context.news_score,
        ),
        strategy=StrategyDetail(
            proposal=strategy.side if strategy is not None else "",
            rationale=strategy.summary[:1_000] if strategy is not None else "",
            gate=("passed" if strategy.gate_passed else "blocked") if strategy is not None else "",
            blockers=tuple(blocker[:240] for blocker in strategy.blockers)
            if strategy is not None
            else (),
            conviction=strategy.conviction if strategy is not None else None,
        ),
        critic=CriticDetail(
            verdict=critic.decision if critic is not None else "",
            rationale=(critic.objection or "")[:1_000] if critic is not None else "",
            layer=critic.decided_layer if critic is not None else "",
        ),
        roles=_role_details(context),
    )


def _role_details(context: _DetailContext) -> tuple[RoleDetail, ...]:
    """Project the decision-relevant 01--11 outputs into bounded display rows."""
    completed = {
        str(getattr(stage, "component", "")): str(getattr(stage, "summary", ""))[:1_000]
        for stage in context.stages
    }
    universe = context.universe_output
    technical = context.technical_output
    daily = context.daily_screener_output
    macro = context.macro_output
    disclosure = context.disclosure_source
    news = context.news_source
    disclosure_output = context.disclosure_output
    news_output = context.news_output
    disclosure_analysis = context.disclosure_analysis
    news_analysis = context.news_analysis
    strategy = context.strategy_output
    critic = context.critic_verdict
    order = context.order
    review = context.review
    return (
        _role(
            "01",
            "유니버스 스크리너",
            completed,
            _universe_facts(universe),
            _universe_items(universe),
        ),
        _role(
            "02",
            "기술 분석",
            completed,
            _technical_facts(technical),
            _technical_items(technical),
        ),
        _role("03", "일일 스크리너", completed, _daily_facts(daily), _daily_items(daily)),
        _role("04", "매크로 분석", completed, _macro_facts(macro)),
        _role(
            "05",
            "공시 분석",
            completed,
            _disclosure_facts(disclosure, disclosure_output, disclosure_analysis),
            _disclosure_items(context.disclosure_sources, disclosure),
        ),
        RoleDetail(
            component="06",
            title="뉴스 분석",
            status="completed" if "06" in completed else "pending",
            summary=completed.get("06", ""),
            facts=_news_facts(news, news_output, news_analysis),
            news_selection=_news_selection(context.news_sources, news),
        ),
        _role("07", "전략가", completed, _strategy_facts(strategy)),
        _role("08", "비평가", completed, _critic_facts(critic)),
        _role("09", "리스크·포트폴리오", completed, _risk_facts(context)),
        _role("10", "주문·체결", completed, _order_facts(order)),
        _role(
            "11",
            "T+5 리뷰",
            completed,
            _review_facts(review),
            (
                "T+1~T+5 종가와 수익률을 추적합니다.",
                "T+5 장 마감 후 최대 낙폭과 판단 일치도를 평가해 교훈을 기록합니다.",
            ),
        ),
    )


def _disclosure_items(
    values: tuple[_DisclosureDetailSource, ...], selected: _DisclosureDetailSource | None
) -> tuple[str, ...]:
    return tuple(
        " · ".join(
            (
                "대표 분석" if value is selected else "수집",
                value.form_type,
                value.title,
                f"제출 {value.filed_at}",
                f"출처 {_safe_external_reference(value.source_ref)}",
            )
        )
        for value in values
    )


def _news_selection(
    values: tuple[_NewsDetailSource, ...], selected: _NewsDetailSource | None
) -> NewsSelectionDetail:
    return NewsSelectionDetail(
        items=tuple(
            NewsSelectionDetailItem(
                status=(
                    NewsMatchStatus.SELECTED
                    if value is selected
                    else _unselected_news_status(value.selection_status)
                ),
                is_representative=value is selected,
                score=value.relevance_score,
                reasons=value.relevance_reasons,
                relevance_evaluated=value.selection_status is not NewsMatchStatus.FETCHED,
                representative_label=(
                    "관련성 규칙 대표 뉴스"
                    if value is selected and value.selection_status is NewsMatchStatus.SELECTED
                    else "분석에 사용된 대표 소스"
                    if value is selected
                    else ""
                ),
                representative_explanation=(
                    "종목·기업명 관련성 점수와 발행 시각의 결정론적 순위로 선정했습니다."
                    if value is selected and value.selection_status is NewsMatchStatus.SELECTED
                    else (LEGACY_REPRESENTATIVE_EXPLANATION if value is selected else "")
                ),
                title=value.title,
                published_at=str(value.published_at),
                reference=_safe_external_reference(value.url),
            )
            for value in values
        )
    )


def _unselected_news_status(status: NewsMatchStatus) -> NewsMatchStatus:
    match status:
        case NewsMatchStatus.FETCHED | NewsMatchStatus.SELECTED:
            return NewsMatchStatus.EXCLUDED
        case NewsMatchStatus.RELEVANT | NewsMatchStatus.EXCLUDED:
            return status
        case unreachable:
            assert_never(unreachable)


def _safe_external_reference(reference: str) -> str:
    try:
        parsed = urlsplit(reference)
        port = parsed.port
    except ValueError:
        return "invalid reference"
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return "invalid reference"
    safe_port = f":{port}" if port is not None else ""
    sanitized = urlunsplit((parsed.scheme, f"{parsed.hostname}{safe_port}", parsed.path, "", ""))
    if len(sanitized) <= DISPLAY_REFERENCE_MAX_LENGTH:
        return sanitized
    digest = hashlib.sha256(sanitized.encode()).hexdigest()
    return f"long-reference:sha256:{digest}"


def _role(
    component: str,
    title: str,
    completed: dict[str, str],
    facts: tuple[tuple[str, str], ...] = (),
    items: tuple[str, ...] = (),
) -> RoleDetail:
    summary = completed.get(component, "")
    return RoleDetail(
        component=component,
        title=title,
        status="completed" if component in completed else "pending",
        summary=summary,
        facts=facts,
        items=items,
    )


def _facts(*pairs: tuple[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple((label, str(value)[:1_000]) for label, value in pairs if value not in (None, ""))


def _item(value: str) -> str:
    return value


def _universe_facts(value: UniverseScreenerOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return _facts(("종목 수", 0))
    return _facts(("종목 수", len(value.members)))


def _universe_items(value: UniverseScreenerOutput | None) -> tuple[str, ...]:
    members = value.members if value is not None else ()
    return tuple(
        _item(
            " · ".join(
                (
                    item.ticker,
                    item.company_name,
                    f"기준일 {item.as_of_date}",
                    f"시가총액 {item.market_cap}",
                    f"근거 {','.join(item.evidence_ids)}",
                )
            )
        )
        for item in members
    )


def _technical_facts(value: TechnicalAnalysisOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return _facts(("분석 스냅샷", 0), ("제외 종목", "-"))
    return _facts(
        ("분석 스냅샷", len(value.snapshots)),
        ("제외 종목", ", ".join(value.excluded_insufficient_history) or "없음"),
    )


def _technical_items(value: TechnicalAnalysisOutput | None) -> tuple[str, ...]:
    snapshots = value.snapshots if value is not None else ()
    return tuple(
        _item(
            " · ".join(
                (
                    item.ticker,
                    f"종가 {item.close}",
                    f"거래일 {item.trade_date}",
                    f"RS20 {item.rs_20}",
                    f"거래량비 {item.vol_ratio}",
                    f"5일 수익률 {item.ret_5d}",
                    f"20일 수익률 {item.ret_20d}",
                    f"ATR% {item.atr_pct}",
                    f"52주고점비 {item.high_252_ratio}",
                    f"RSI {item.rsi}",
                    f"MACD {item.macd}",
                    f"MA20 {item.ma20}",
                    f"MA50 {item.ma50}",
                    f"추세 {item.trend}",
                    f"ML {item.ml_probs}",
                    f"근거 {','.join(item.evidence_ids)}",
                )
            )
        )
        for item in snapshots
    )


def _daily_facts(value: DailyScreenerOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return _facts(("선정 종목", 0))
    return _facts(("선정 종목", len(value.picks)))


def _daily_items(value: DailyScreenerOutput | None) -> tuple[str, ...]:
    picks = value.picks if value is not None else ()
    return tuple(
        _item(
            " · ".join(
                (
                    f"#{item.rank} {item.ticker}",
                    str(item.bucket),
                    item.sector,
                    f"점수 {item.score}",
                    "사용자 요청 심층 분석" if item.is_requested_focus else "정량 상위 후보",
                    f"거래일 {item.trade_date}",
                    f"유니버스 기준일 {item.universe_as_of}",
                    f"근거 {','.join(item.evidence_ids)}",
                )
            )
        )
        for item in picks
    )


def _macro_facts(value: MacroAnalysisOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    return _facts(
        ("국면", value.regime),
        ("기준 시각", value.as_of),
        ("리스크 점수", value.risk_score),
        ("VIX", value.vix),
        ("NASDAQ 수익률", value.nasdaq_ret),
        ("S&P 500 수익률", value.sp500_ret),
        ("금리", value.rate),
        ("달러", value.dollar),
        ("근거", ", ".join(value.evidence_ids)),
    )


def _disclosure_facts(
    value: _DisclosureDetailSource | None,
    output: DisclosureSignal | None,
    analysis: AnalysisResult | None,
) -> tuple[tuple[str, str], ...]:
    if value is None and output is None and analysis is None:
        return ()
    return _facts(
        ("제목", value.title if value else None),
        ("양식", value.form_type if value else None),
        ("이벤트", output.event_type if output else value.event_type if value else None),
        ("제출일", output.filed_at if output else value.filed_at if value else None),
        ("신호", output.has_signal if output else None),
        ("중요도", output.importance if output else None),
        ("감성", output.sentiment_score if output else None),
        ("위험", output.risk_score if output else None),
        ("신뢰도", output.confidence if output else value.confidence if value else None),
        ("모델 판정", analysis.label if analysis else None),
        ("모델 점수", analysis.score if analysis else None),
        ("분석 이유", output.reason if output else analysis.reason if analysis else None),
        ("모델", analysis.metadata.model if analysis else None),
        ("제공자", analysis.metadata.provider if analysis else None),
        ("프롬프트 버전", analysis.metadata.prompt_version if analysis else None),
        ("정책 버전", analysis.metadata.policy_version if analysis else None),
        ("하드 차단", output.is_hard_blocked if output else None),
        ("차단 이유", output.hard_block_reason if output else None),
        ("요약", output.summary if output else value.summary if value else None),
        ("공시 번호", output.filing_no if output else None),
    )


def _news_facts(
    value: _NewsDetailSource | None,
    output: NewsSignal | None,
    analysis: AnalysisResult | None,
) -> tuple[tuple[str, str], ...]:
    if value is None and output is None and analysis is None:
        return ()
    return _facts(
        ("제목", value.title if value else None),
        ("출처", output.source if output else value.source if value else None),
        ("발행 시각", output.published_at if output else value.published_at if value else None),
        ("뉴스 수", output.news_count if output else None),
        ("이벤트", output.event_type if output else None),
        ("중요도", output.importance if output else None),
        ("최고 중요도", output.peak_importance if output else None),
        ("감성", output.sentiment_score if output else None),
        ("위험", output.risk_score if output else None),
        ("출처 신뢰", output.source_trust if output else None),
        ("등급 점수", output.grade_score if output else None),
        ("신뢰도", output.confidence if output else value.confidence if value else None),
        ("모델 판정", analysis.label if analysis else None),
        ("모델 점수", analysis.score if analysis else None),
        ("분석 이유", output.reason if output else analysis.reason if analysis else None),
        ("모델", analysis.metadata.model if analysis else None),
        ("제공자", analysis.metadata.provider if analysis else None),
        ("프롬프트 버전", analysis.metadata.prompt_version if analysis else None),
        ("정책 버전", analysis.metadata.policy_version if analysis else None),
        ("하드 차단", output.is_hard_blocked if output else None),
        ("차단 이유", output.hard_block_reason if output else None),
        ("요약", output.summary if output else value.summary if value else None),
    )


def _strategy_facts(value: _StrategyDetailOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    return _facts(
        ("제안", value.side),
        ("확신도", value.conviction),
        ("코드 게이트", "통과" if value.gate_passed else "차단"),
        ("차단 요인", ", ".join(value.blockers) or "없음"),
        ("판단 근거", value.summary),
    )


def _critic_facts(value: _CriticDetailOutput | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    return _facts(
        ("판정", value.decision),
        ("카테고리", value.category),
        ("확신도", value.confidence),
        ("결정 계층", value.decided_layer),
        ("출처 상태", value.source),
        ("건너뛴 규칙", ", ".join(value.skipped_rules) or "없음"),
        ("반대 근거", value.objection),
    )


def _risk_facts(context: _DetailContext) -> tuple[tuple[str, str], ...]:
    if context.risk_decision is None:
        return ()
    return _facts(
        ("계획", context.risk_decision),
        ("신호 ID", context.signal_id),
        ("계정 ID", context.account_id),
        ("수량", context.quantity),
        ("진입가", context.risk_entry_price),
        ("손절가", context.stop_loss),
        ("익절가", context.take_profit),
        ("보류 사유", context.risk_skipped_reason),
    )


def _order_facts(value: OrderResult | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    return _facts(
        ("상태", value.status),
        ("수량", value.quantity),
        ("평균 체결가", value.filled_avg_price),
        ("주문 ID", value.order_id),
        ("클라이언트 주문 ID", value.client_order_id),
    )


def _review_facts(value: ReviewResult | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    return _facts(("결과", value.outcome), ("리뷰", value.summary))
