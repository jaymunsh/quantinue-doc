"""Redacted projection helpers shared by control-room HTTP surfaces."""

from typing import Final
from urllib.parse import urlsplit, urlunsplit

from quantinue.api.live_progress import (
    STAGE_NAME_BY_COMPONENT,
    live_stage_views,
    ui_stage_status,
)
from quantinue.api.schemas import (
    AttemptView,
    CollectionDetailView,
    ControlRoomRun,
    CriticDetailView,
    EvidenceView,
    NewsSelectionItemView,
    NewsSelectionView,
    OrderView,
    PortfolioAccountView,
    PortfolioPositionView,
    ReviewView,
    RoleDetailView,
    SimulatedFillView,
    SimulatedOrderView,
    SimulatedPortfolioView,
    SourceReferenceView,
    StageView,
    StrategyDetailView,
    TerminalRunDetailView,
)
from quantinue.core.contracts import PipelineRun, StageStatus
from quantinue.core.plain_text import plain_text
from quantinue.core.terminal_detail import CollectionFact, NewsSelectionDetail, TerminalRunDetail
from quantinue.db.contracts import PersistedAttempt
from quantinue.db.simulated_portfolio import SimulatedPortfolioSnapshot
from quantinue.market_data.models import NewsMatchStatus

ASCII_CONTROL_LIMIT: Final = 32
RESERVED_FIXTURE_SUFFIX: Final = ".invalid"
LEGACY_EXPLANATION_PREFIX: Final = "기존 실행은 관련성 선별 점수를 기록하지 않았으며,"
LEGACY_EXPLANATION_SUFFIX: Final = "실제 모델 분석에 사용된 소스를 대표 항목으로 표시합니다."
LEGACY_REPRESENTATIVE_EXPLANATION: Final = (
    f"{LEGACY_EXPLANATION_PREFIX} {LEGACY_EXPLANATION_SUFFIX}"
)
ROLE_DESCRIPTIONS: Final[dict[str, str]] = {
    "01": (
        "NASDAQ 기업의 종목명·시가총액·가격·거래량을 확인해 1차 MVP 분석 유니버스 "
        "50개를 안정적으로 선정·보존하고 후속 기술 분석 대상으로 전달합니다."
    ),
    "02": (
        "1차 유니버스 50개 중 최대 20개 종목의 실제 일봉 가격과 거래량으로 "
        "추세·모멘텀·변동성 지표를 계산합니다. "
        "이동평균, RSI, MACD, 상대강도, ATR과 수익률을 다음 단계의 정량 근거로 전달합니다."
    ),
    "03": (
        "20개 기술 분석 결과를 결정론적으로 순위화해 오늘 상세 분석할 후보 20개를 확정합니다. "
        "선정된 후보 각각을 공시·뉴스 수집부터 전략·비평·리스크·모의 주문까지의 "
        "심층 분석으로 전달합니다."
    ),
    "04": (
        "금리·주가지수·변동성·달러 등 거시 지표를 종합해 시장 국면과 위험 점수를 "
        "판정합니다. 위험 회피 국면은 이후 전략과 주문의 하드 게이트로 사용됩니다."
    ),
    "05": (
        "SEC 공시 계약과 같은 양식·제출 시각·문서 필드를 바탕으로 중요도, 감성, 위험과 "
        "차단 사유를 분석합니다. 실행 모드에 맞는 출처와 모델·프롬프트 계보를 함께 남깁니다."
    ),
    "06": (
        "뉴스 계약의 제목·요약·발행 시각을 바탕으로 사건 유형, 중요도, 감성, 위험도와 "
        "출처 신뢰도를 분석합니다. 실행 모드에 맞는 수집 출처와 선별 이유를 함께 남깁니다."
    ),
    "07": (
        "기술·거시·공시·뉴스 근거를 종합해 매수·보유 제안과 확신도를 만듭니다. "
        "모델 판단과 별개로 코드 정책 게이트를 적용하고 차단 요인을 명시합니다."
    ),
    "08": (
        "전략가의 판단을 독립적으로 반박·검증합니다. 하드 규칙, 근거 충돌, 신뢰도 "
        "부족과 위험 신호를 확인해 승인 또는 거절 판정을 내립니다."
    ),
    "09": (
        "비평가 승인과 계좌 규칙을 적용합니다. 주문 여부를 판정하고 "
        "수량·진입가·손절가·익절가를 정합니다."
    ),
    "10": (
        "주문안을 설정된 브로커 계약으로 처리합니다. 로컬 모드에서는 외부 전송 없이 "
        "체결 결과와 주문번호를 만듭니다."
    ),
    "11": (
        "이번 실행의 판단 결과를 사후 검토 대상으로 등록합니다. 내장 스케줄러는 없지만 "
        "PostgreSQL 모드에서 운영자가 처리 API를 호출하면 T+1~T+5 가격과 수익률·최대 낙폭·"
        "판단 일치도를 멱등 처리해 실제 결과를 학습 근거로 남깁니다."
    ),
}


def attempt_view(attempt: PersistedAttempt) -> AttemptView:
    """Project a persisted attempt without its raw error message."""
    duration_ms = None
    if attempt.finished_at is not None:
        elapsed = (attempt.finished_at - attempt.started_at).total_seconds()
        duration_ms = max(0, round(elapsed * 1000))
    return AttemptView(
        attempt_no=attempt.attempt_no,
        status=attempt.status,
        started_at=attempt.started_at,
        finished_at=attempt.finished_at,
        duration_ms=duration_ms,
        failure_code=attempt.error_code,
    )


def source_reference_view(reference: str) -> SourceReferenceView:
    """Allow only credential-free absolute HTTP(S) references to be browser links."""
    if any(character.isspace() or ord(character) < ASCII_CONTROL_LIMIT for character in reference):
        return SourceReferenceView(label="invalid reference")
    try:
        parsed = urlsplit(reference)
        _ = parsed.port
    except ValueError:
        return SourceReferenceView(label="invalid reference")
    is_web = parsed.scheme.lower() in {"http", "https"}
    has_credentials = parsed.username is not None or parsed.password is not None
    has_host = parsed.hostname is not None
    is_reserved_fixture = parsed.hostname is not None and parsed.hostname.casefold().endswith(
        RESERVED_FIXTURE_SUFFIX
    )
    if is_web and has_host and not has_credentials and not is_reserved_fixture:
        safe_port = f":{parsed.port}" if parsed.port is not None else ""
        label = urlunsplit((parsed.scheme, f"{parsed.hostname}{safe_port}", parsed.path, "", ""))
        href = label
    elif is_web and has_host:
        safe_port = f":{parsed.port}" if parsed.port is not None else ""
        label = urlunsplit((parsed.scheme, f"{parsed.hostname}{safe_port}", parsed.path, "", ""))
        href = None
    elif parsed.scheme.lower() in {"data", "javascript"}:
        label = "non-web reference"
        href = None
    elif is_web and not has_host:
        label = "invalid reference"
        href = None
    else:
        label = reference
        href = None
    return SourceReferenceView(label=label, href=href)


def evidence_reference_label(reference: str) -> str:
    """Return a trace-safe reference label without transport credentials or fragments."""
    safe_reference = source_reference_view(reference).label
    try:
        parsed = urlsplit(safe_reference)
        _ = parsed.port
    except ValueError:
        return "invalid reference"
    safe_netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))


def _collection_detail_view(fact: CollectionFact) -> CollectionDetailView:
    """Project one already-bounded collection fact into its API representation."""
    return CollectionDetailView(
        title=plain_text(fact.title),
        summary=plain_text(fact.summary),
        source=fact.source,
        reference=source_reference_view(fact.reference),
        score=fact.score,
    )


def _news_selection_view(detail: NewsSelectionDetail) -> NewsSelectionView:
    projected_items: list[NewsSelectionItemView] = []
    for item in detail.items:
        is_legacy_representative = item.is_representative and item.score == 0 and not item.reasons
        projected_items.append(
            NewsSelectionItemView(
                status=item.status,
                is_representative=item.is_representative,
                score=item.score,
                reasons=tuple(str(reason) for reason in item.reasons),
                relevance_evaluated=item.relevance_evaluated and not is_legacy_representative,
                representative_label=(
                    item.representative_label
                    or ("분석에 사용된 대표 소스" if is_legacy_representative else "")
                ),
                representative_explanation=(
                    item.representative_explanation
                    or (LEGACY_REPRESENTATIVE_EXPLANATION if is_legacy_representative else "")
                ),
                title=plain_text(item.title),
                published_at=item.published_at,
                reference=source_reference_view(item.reference),
            )
        )
    projected = tuple(projected_items)
    representative_count = sum(item.is_representative for item in projected)
    relevant_count = sum(
        item.status in {NewsMatchStatus.SELECTED, NewsMatchStatus.RELEVANT} for item in projected
    )
    return NewsSelectionView(
        fetched_count=len(projected),
        relevant_count=relevant_count,
        excluded_count=sum(item.status is NewsMatchStatus.EXCLUDED for item in projected),
        representative_count=representative_count,
        items=projected,
    )


def simulated_portfolio_view(snapshot: SimulatedPortfolioSnapshot) -> SimulatedPortfolioView:
    """Project the local buy-only ledger without implying an Alpaca account balance."""
    return SimulatedPortfolioView(
        account=PortfolioAccountView(
            opening_cash=snapshot.account.opening_cash,
            current_cash=snapshot.account.current_cash,
            equity=snapshot.account.equity,
            buying_power=snapshot.account.buying_power,
            currency=snapshot.account.currency,
        ),
        positions=tuple(
            PortfolioPositionView(
                ticker=position.ticker,
                quantity=position.quantity,
                average_cost=position.average_cost,
                mark_price=position.mark.price,
                mark_source=position.mark.source.value,
                mark_as_of=position.mark.as_of,
                market_value=position.market_value,
                unrealized_pnl=position.unrealized_pnl,
                allocation=position.allocation,
            )
            for position in snapshot.positions
        ),
        orders=tuple(
            SimulatedOrderView(
                order_id=order.order_id,
                ticker=order.ticker,
                quantity=order.quantity,
                reference_price=order.reference_price,
                status=order.status.value,
                created_at=order.created_at,
            )
            for order in snapshot.orders
        ),
        fills=tuple(
            SimulatedFillView(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                ticker=fill.ticker,
                quantity=fill.quantity,
                price=fill.price,
                filled_at=fill.filled_at,
            )
            for fill in snapshot.fills
        ),
        realized_pnl_label="해당 없음 · 1차 매수 전용",
    )


def terminal_run_detail_view(detail: TerminalRunDetail) -> TerminalRunDetailView:
    """Project bounded terminal detail without adding raw execution material."""
    return TerminalRunDetailView(
        disclosure=_collection_detail_view(detail.disclosure),
        news=_collection_detail_view(detail.news),
        strategy=StrategyDetailView(
            proposal=detail.strategy.proposal,
            rationale=detail.strategy.rationale,
            gate=detail.strategy.gate,
            blockers=detail.strategy.blockers,
            conviction=detail.strategy.conviction,
        ),
        critic=CriticDetailView(
            verdict=detail.critic.verdict,
            rationale=detail.critic.rationale,
            layer=detail.critic.layer,
        ),
        roles=tuple(
            RoleDetailView(
                component=role.component,
                title=role.title,
                description=ROLE_DESCRIPTIONS[role.component],
                status=role.status,
                summary=plain_text(role.summary),
                facts=tuple((plain_text(label), plain_text(value)) for label, value in role.facts),
                items=tuple(plain_text(item) for item in role.items),
                news_selection=(
                    _news_selection_view(role.news_selection)
                    if role.component == "06" and role.news_selection is not None
                    else None
                ),
            )
            for role in detail.roles
        ),
    )


def control_room_run(run: PipelineRun, attempts: tuple[PersistedAttempt, ...]) -> ControlRoomRun:
    """Build the shared, redacted API and server-rendered observability view."""
    attempts_by_component: dict[str, list[PersistedAttempt]] = {}
    for attempt in attempts:
        attempts_by_component.setdefault(attempt.component, []).append(attempt)
    results_by_component = {stage.component: stage for stage in run.stages}
    components = tuple(dict.fromkeys((*results_by_component, *attempts_by_component)))
    stage_views: list[StageView] = []
    for component in components:
        result = results_by_component.get(component)
        component_attempts = tuple(
            attempt_view(attempt) for attempt in attempts_by_component.get(component, [])
        )
        latest_attempt = component_attempts[-1] if component_attempts else None
        stage_status = result.status if result is not None else StageStatus.PENDING
        if latest_attempt is not None and latest_attempt.status != "completed":
            stage_status = ui_stage_status(latest_attempt.status)
        stage_views.append(
            StageView(
                component=component,
                name=result.name
                if result is not None
                else STAGE_NAME_BY_COMPONENT.get(component, f"Stage {component}"),
                status=stage_status,
                summary=result.summary if result is not None else "완료 전 실행 관측",
                attempts=component_attempts,
                duration_ms=sum(attempt.duration_ms or 0 for attempt in component_attempts) or None,
                checkpointed=result is not None and result.status is StageStatus.COMPLETED,
                failure_code=latest_attempt.failure_code if latest_attempt is not None else None,
            )
        )
    evidence = tuple(
        EvidenceView(
            evidence_id=item.evidence_id,
            component=item.component,
            source=item.source,
            source_ref=evidence_reference_label(item.source_ref),
            observed_at=item.observed_at,
            captured_at=item.captured_at,
            confidence=item.confidence,
            parent_evidence_ids=item.parent_evidence_ids,
            model_name=item.model_name,
            model_provider=item.model_provider,
            prompt_version=item.prompt_version,
            policy_version=item.policy_version,
            input_hash=item.input_hash,
        )
        for item in run.evidence_trace
    )
    order = (
        OrderView(
            order_id=run.order.order_id,
            client_order_id=run.order.client_order_id,
            reconciliation_status=run.order.status,
            quantity=run.order.quantity,
            filled_avg_price=run.order.filled_avg_price,
        )
        if run.order is not None
        else None
    )
    review = (
        ReviewView(outcome=run.review.outcome, summary=run.review.summary)
        if run.review is not None
        else None
    )
    current_stage, next_stage = live_stage_views(run, attempts)
    stage_statuses = {stage.component: stage.status.value for stage in stage_views}
    detail_view = terminal_run_detail_view(run.detail)
    detail_view = detail_view.model_copy(
        update={
            "roles": tuple(
                role.model_copy(update={"status": stage_statuses.get(role.component, role.status)})
                for role in detail_view.roles
            )
        }
    )
    return ControlRoomRun(
        run_id=run.run_id,
        ticker=run.ticker,
        cycle_ts=run.cycle_ts,
        status=run.status,
        progress=len(run.stages),
        current_stage=current_stage,
        next_stage=next_stage,
        stages=tuple(stage_views),
        evidence=evidence,
        conviction=run.conviction,
        side=run.side,
        detail=detail_view,
        order=order,
        review=review,
        automatic=run.automatic,
        candidate_rank=run.candidate_rank,
    )
