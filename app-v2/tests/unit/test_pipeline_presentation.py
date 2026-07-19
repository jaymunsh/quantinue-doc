"""Phase 5: the job-shaped control room view — what did today's pipeline do?"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quantinue.api.pipeline_presentation import (
    allocation_view,
    chain_view,
    equity_curve_views,
    profile_judgement_views,
    sparkline_points,
)
from quantinue.db.control_room_reads import (
    AccountEquityPoint,
    JobRunRecord,
    JudgementRecord,
    OrderPlanRecord,
)

_DAY = date(2026, 7, 20)
_START = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


def _job(
    name: str,
    *,
    status: str = "succeeded",
    detail: str | None = "ok",
    offset_minutes: int = 0,
    duration_seconds: int | None = 30,
) -> JobRunRecord:
    started = _START + timedelta(minutes=offset_minutes)
    return JobRunRecord(
        job_name=name,
        slot_date=_DAY,
        status=status,
        detail=detail,
        started_at=started,
        finished_at=None
        if duration_seconds is None
        else started + timedelta(seconds=duration_seconds),
    )


def test_a_finished_job_reports_how_long_it_took() -> None:
    # Given
    records = (_job("universe", duration_seconds=45),)

    # When
    view = chain_view(_DAY, records)

    # Then
    assert view.jobs[0].duration_ms == 45_000


def test_a_running_job_has_no_duration_yet() -> None:
    """도는 중인 잡에 소요시간을 지어내면 끝난 것처럼 읽힌다."""
    # Given
    records = (_job("universe", status="running", duration_seconds=None),)

    # When
    view = chain_view(_DAY, records)

    # Then
    assert view.jobs[0].duration_ms is None
    assert view.running == 1


def test_the_chain_names_where_it_broke() -> None:
    """체인은 순서가 계약이므로 '어디서 끊겼나'가 상태 요약보다 중요하다."""
    # Given
    records = (
        _job("universe", offset_minutes=0),
        _job("daily_bars", status="failed", detail="alpaca 400", offset_minutes=1),
        _job("screening", offset_minutes=2),
    )

    # When
    view = chain_view(_DAY, records)

    # Then
    assert view.broke_at == "daily_bars"
    assert view.failed == 1
    assert view.succeeded == 2


def test_a_whole_chain_that_worked_broke_nowhere() -> None:
    # Given
    records = (_job("universe"), _job("daily_bars", offset_minutes=1))

    # When
    view = chain_view(_DAY, records)

    # Then
    assert view.broke_at is None
    assert view.succeeded == 2


def test_an_empty_day_is_a_view_not_a_crash() -> None:
    """잡이 한 번도 안 돈 설치에서도 화면은 떠야 한다."""
    # When
    view = chain_view(None, ())

    # Then
    assert view.slot_date is None
    assert view.jobs == ()
    assert view.broke_at is None


def _plan(
    ticker: str,
    *,
    decision: str = "planned",
    reason: str | None = None,
    account_id: int | None = 1,
    quantity: int = 10,
) -> OrderPlanRecord:
    return OrderPlanRecord(
        ticker=ticker,
        account_id=account_id,
        trade_date=_DAY,
        decision=decision,
        skipped_reason=reason,
        quantity=quantity,
        entry_price=Decimal(50) if decision == "planned" else None,
    )


def test_allocation_counts_what_was_bought_and_what_was_not() -> None:
    # Given
    records = (
        _plan("AAA"),
        _plan("BBB", decision="skipped", reason="min_cash", quantity=0),
    )

    # When
    view = allocation_view(records)

    # Then
    assert view.bought == 1
    assert view.skipped == 1


def test_skip_reasons_are_ranked_by_how_often_they_fired() -> None:
    """문턱 조정의 입력이다 — 어느 게이트가 실제로 막고 있는지가 먼저 보여야 한다."""
    # Given
    records = (
        _plan("AAA", decision="skipped", reason="min_cash", quantity=0),
        _plan("BBB", decision="skipped", reason="min_cash", quantity=0),
        _plan("CCC", decision="skipped", reason="daily_order_cap", quantity=0),
    )

    # When
    view = allocation_view(records)

    # Then
    assert [item.reason for item in view.reasons] == ["min_cash", "daily_order_cap"]
    assert view.reasons[0].count == 2


def test_a_day_with_no_allocation_rows_reports_zero_not_nothing() -> None:
    # When
    view = allocation_view(())

    # Then
    assert view.bought == 0
    assert view.skipped == 0
    assert view.reasons == ()


def test_an_account_curve_reports_its_change_over_the_window() -> None:
    # Given
    points = (
        AccountEquityPoint(account_id=1, trade_date=_DAY - timedelta(days=1), equity=Decimal(1000)),
        AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(1100)),
    )

    # When
    views = equity_curve_views(points)

    # Then
    assert views[0].opening_equity == Decimal(1000)
    assert views[0].latest_equity == Decimal(1100)
    assert views[0].change_pct == Decimal("10.00")


def test_a_single_point_curve_has_not_moved() -> None:
    """하루치만 있는 계좌에 변화율을 지어내면 안 된다 — 비교 대상이 없다."""
    # Given
    points = (AccountEquityPoint(account_id=7, trade_date=_DAY, equity=Decimal(500)),)

    # When
    views = equity_curve_views(points)

    # Then
    assert views[0].change_pct == Decimal("0.00")


def test_accounts_are_separated_even_when_their_points_interleave() -> None:
    # Given
    points = (
        AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(100)),
        AccountEquityPoint(account_id=2, trade_date=_DAY, equity=Decimal(200)),
    )

    # When
    views = equity_curve_views(points)

    # Then
    assert [view.account_id for view in views] == [1, 2]


def test_a_flat_curve_draws_down_the_middle_not_along_the_floor() -> None:
    """0으로 나눌 수 없는 구간을 바닥에 붙이면 전액 손실처럼 보인다."""
    # Given
    points = (
        AccountEquityPoint(account_id=1, trade_date=_DAY - timedelta(days=1), equity=Decimal(500)),
        AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(500)),
    )
    curve = equity_curve_views(points)[0]

    # When
    plotted = sparkline_points(curve, width=100, height=20)

    # Then
    assert plotted == "0.0,10.0 100.0,10.0"


def test_a_rising_curve_ends_higher_on_screen_than_it_started() -> None:
    # Given
    points = (
        AccountEquityPoint(account_id=1, trade_date=_DAY - timedelta(days=1), equity=Decimal(100)),
        AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(200)),
    )
    curve = equity_curve_views(points)[0]

    # When
    plotted = sparkline_points(curve, width=100, height=20)

    # Then — SVG는 y가 아래로 자라므로 오른 곡선의 끝점 y가 더 작다
    assert plotted == "0.0,20.0 100.0,0.0"


def test_a_one_point_curve_is_not_plotted() -> None:
    # Given
    points = (AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(100)),)
    curve = equity_curve_views(points)[0]

    # When / Then
    assert sparkline_points(curve) == ""


def _judgement(
    ticker: str,
    *,
    inv_type: str = "aggressive",
    side: str = "buy",
    verdict: str | None = "pass",
    conviction: str = "0.800",
) -> JudgementRecord:
    return JudgementRecord(
        ticker=ticker,
        inv_type=inv_type,
        side=side,
        conviction=Decimal(conviction),
        summary="fixture",
        bull_case=None,
        key_risk=None,
        verdict_decision=verdict,
        verdict_confidence=None if verdict is None else Decimal("0.700"),
        objection=None if verdict is None else "반박문",
    )


def test_judgements_split_by_profile_with_their_approval_counts() -> None:
    """성향 격차는 이 시스템의 핵심 주장이라 화면에서 갈려 보여야 한다."""
    # Given
    records = (
        _judgement("AAA", inv_type="aggressive", verdict="pass"),
        _judgement("BBB", inv_type="aggressive", verdict="reject"),
        _judgement("CCC", inv_type="conservative", verdict="reject"),
    )

    # When
    views = profile_judgement_views(records)

    # Then
    by_profile = {view.inv_type: view for view in views}
    assert by_profile["aggressive"].approved == 1
    assert by_profile["aggressive"].total == 2
    assert by_profile["conservative"].approved == 0


def test_an_unjudged_signal_counts_as_neither_approved_nor_rejected() -> None:
    """평결 전에 끊긴 판단을 기각으로 세면 크리틱이 하지 않은 일을 뒤집어쓴다."""
    # Given
    records = (_judgement("AAA", verdict=None),)

    # When
    views = profile_judgement_views(records)

    # Then
    assert views[0].approved == 0
    assert views[0].unjudged == 1
    assert views[0].judgements[0].verdict_decision is None
