from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantinue.roles.role_11_reviewer.calendar import UsEquityTradingCalendar
from quantinue.roles.role_11_reviewer.contracts import (
    ReviewInput,
    ReviewOutput,
    ReviewPriceSnapshot,
    ReviewSignal,
)
from quantinue.roles.role_11_reviewer.service import (
    ReviewNotReadyError,
    ReviewScheduler,
    ReviewScorer,
)

_DECIDED_AT = datetime(2026, 7, 13, 19, 0, tzinfo=UTC)
_CAPTURED_AT = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def signal(*, signal_id: int, side: str, filled: int | None = None) -> ReviewSignal:
    return ReviewSignal.model_validate(
        {
            "run_id": "run-11",
            "signal_id": signal_id,
            "side": side,
            "trade_date": date(2026, 7, 13),
            "decided_at": _DECIDED_AT,
            "evidence_ids": ("signal-evidence",),
            "not_applicable": (
                ({"dimension": "filled_price", "reason": "hold has no broker fill"},)
                if side == "hold"
                else ()
            ),
            "filled_price": Decimal(filled) if filled is not None else None,
            "decision_close": Decimal(100),
        },
        strict=True,
    )


def snapshot(offset: int, close: int, *, price_date: date | None = None) -> ReviewPriceSnapshot:
    calendar = UsEquityTradingCalendar()
    session_date = price_date or calendar.offset(date(2026, 7, 13), trading_days=offset)
    return ReviewPriceSnapshot(
        run_id="run-11",
        evidence_id=f"price-{offset}",
        parent_evidence_ids=("signal-evidence",),
        day_offset=offset,
        price_date=session_date,
        close=Decimal(close),
        observed_at=calendar.session_close(session_date),
        captured_at=_CAPTURED_AT,
    )


def test_fifth_trading_day_skips_weekend_and_independence_day() -> None:
    # Given
    calendar = UsEquityTradingCalendar()
    # When
    due_date = calendar.offset(date(2026, 7, 1), trading_days=5)
    # Then
    assert due_date == date(2026, 7, 9)


def test_t_plus_five_spans_dst_and_weekend() -> None:
    # Given
    calendar = UsEquityTradingCalendar()
    # When
    due_date = calendar.offset(date(2026, 10, 30), trading_days=5)
    due_close = calendar.session_close(due_date)
    # Then
    assert due_date == date(2026, 11, 6)
    assert due_close == datetime(2026, 11, 6, 21, 0, tzinfo=UTC)


def test_session_close_utc_tracks_new_york_dst() -> None:
    # Given
    calendar = UsEquityTradingCalendar()
    # When
    summer_close = calendar.session_close(date(2026, 7, 13))
    winter_close = calendar.session_close(date(2026, 12, 14))
    # Then
    assert (summer_close.hour, winter_close.hour) == (20, 21)


def test_scheduler_waits_until_fifth_session_close() -> None:
    # Given
    calendar = UsEquityTradingCalendar()
    before = FixedClock(datetime(2026, 7, 20, 19, 59, tzinfo=UTC))
    after = FixedClock(datetime(2026, 7, 20, 20, 0, tzinfo=UTC))
    # When
    before_ready = ReviewScheduler(calendar=calendar, clock=before).is_ready(date(2026, 7, 13))
    after_ready = ReviewScheduler(calendar=calendar, clock=after).is_ready(date(2026, 7, 13))
    # Then
    assert before_ready is False
    assert after_ready is True


def test_buy_and_hold_require_their_contractual_base() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="filled_price"):
        _ = ReviewSignal(
            run_id="run-11",
            signal_id=1,
            side="buy",
            trade_date=date(2026, 7, 13),
            decided_at=_DECIDED_AT,
            evidence_ids=("evidence",),
            not_applicable=(),
            decision_close=Decimal(100),
        )


def test_scorer_calculates_buy_returns_from_fill() -> None:
    # Given
    request = ReviewInput(
        signal=signal(signal_id=1, side="buy", filled=100),
        snapshots=(
            snapshot(1, 101),
            snapshot(2, 98),
            snapshot(3, 103),
            snapshot(4, 99),
            snapshot(5, 104),
        ),
    )
    scorer = ReviewScorer(UsEquityTradingCalendar(), FixedClock(_CAPTURED_AT))
    # When
    result = scorer.score(request, lesson="규칙 기반 회고")
    # Then
    assert (result.ret_1d, result.ret_3d, result.ret_5d) == (1.0, 3.0, 4.0)
    assert result.max_drawdown == -2.0
    assert result.is_hit is True
    assert result.run_id == "run-11"
    assert result.evidence_ids == (
        "signal-evidence",
        "price-1",
        "price-2",
        "price-3",
        "price-4",
        "price-5",
    )


def test_scorer_marks_rising_hold_as_missed_opportunity() -> None:
    # Given
    request = ReviewInput(
        signal=signal(signal_id=2, side="hold"),
        snapshots=tuple(snapshot(offset, 100 + offset) for offset in range(1, 6)),
    )
    scorer = ReviewScorer(UsEquityTradingCalendar(), FixedClock(_CAPTURED_AT))
    # When
    result = scorer.score(request, lesson="보류 기회비용 회고")
    # Then
    assert result.ret_5d == 5.0
    assert result.is_hit is False


def test_scorer_rejects_malformed_session_date() -> None:
    # Given
    request = ReviewInput(
        signal=signal(signal_id=3, side="hold"),
        snapshots=tuple(
            snapshot(offset, 100, price_date=date(2026, 7, 13 + offset)) for offset in range(1, 6)
        ),
    )
    # When / Then
    with pytest.raises(ValueError, match="price_date"):
        _ = ReviewScorer(UsEquityTradingCalendar(), FixedClock(_CAPTURED_AT)).score(
            request, lesson="잘못된 날짜"
        )


def test_review_output_forbids_caller_supplied_numeric_result() -> None:
    # Given
    request = ReviewInput(
        signal=signal(signal_id=4, side="hold"),
        snapshots=tuple(snapshot(offset, 100) for offset in range(1, 6)),
    )
    # When / Then
    with pytest.raises(ValidationError, match="ret_5d"):
        _ = ReviewOutput.model_validate(
            {
                "review_input": request,
                "reviewed_at": _CAPTURED_AT,
                "lesson": "임의 숫자 차단",
                "ret_5d": 999.0,
            },
            strict=True,
        )


def test_scorer_rejects_stale_review_before_t_plus_five_close() -> None:
    # Given
    request = ReviewInput(
        signal=signal(signal_id=5, side="hold"),
        snapshots=tuple(snapshot(offset, 100) for offset in range(1, 6)),
    )
    scorer = ReviewScorer(
        UsEquityTradingCalendar(), FixedClock(datetime(2026, 7, 20, 19, 59, tzinfo=UTC))
    )
    # When / Then
    with pytest.raises(ReviewNotReadyError):
        _ = scorer.score(request, lesson="아직 종가 미확정")


def test_snapshot_rejects_observation_after_capture() -> None:
    # Given
    session_date = date(2026, 7, 14)
    # When / Then
    with pytest.raises(ValidationError, match="observed_at"):
        _ = ReviewPriceSnapshot(
            run_id="run-11",
            evidence_id="future-price",
            parent_evidence_ids=("signal-evidence",),
            day_offset=1,
            price_date=session_date,
            close=Decimal(101),
            observed_at=datetime(2026, 7, 14, 21, 0, tzinfo=UTC),
            captured_at=datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
        )
