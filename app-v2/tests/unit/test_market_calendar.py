"""XNYS calendar adapter: trading days, sessions, and extended-hours windows."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from quantinue.core.market_calendar import NyseCalendar, Session

NY_SUMMER = timezone(timedelta(hours=-4))  # EDT


@pytest.fixture(scope="module")
def calendar() -> NyseCalendar:
    return NyseCalendar()


def test_weekday_is_trading_day_and_weekend_is_not(calendar: NyseCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 20)) is True  # Monday
    assert calendar.is_trading_day(date(2026, 7, 18)) is False  # Saturday


def test_holiday_is_not_a_trading_day(calendar: NyseCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 3)) is False  # Independence Day observed
    assert calendar.is_trading_day(date(2026, 12, 25)) is False


def test_session_open_close_are_utc(calendar: NyseCalendar) -> None:
    opened = calendar.session_open(date(2026, 7, 20))
    closed = calendar.session_close(date(2026, 7, 20))
    assert opened == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)  # 09:30 EDT
    assert closed == datetime(2026, 7, 20, 20, 0, tzinfo=UTC)  # 16:00 EDT


def test_session_open_rejects_non_trading_day(calendar: NyseCalendar) -> None:
    with pytest.raises(ValueError, match="trading"):
        calendar.session_open(date(2026, 7, 18))


def test_add_business_days_skips_weekend(calendar: NyseCalendar) -> None:
    # T+5 from Monday 07-20: Tue,Wed,Thu,Fri,Mon → 07-27.
    assert calendar.add_business_days(date(2026, 7, 20), 5) == date(2026, 7, 27)


def test_add_business_days_skips_holiday(calendar: NyseCalendar) -> None:
    # 07-03 observed holiday: Wed 07-01 + 2 sessions → Mon 07-06.
    assert calendar.add_business_days(date(2026, 7, 1), 2) == date(2026, 7, 6)


def test_dst_transition_days_keep_correct_utc_open(calendar: NyseCalendar) -> None:
    # US DST 2026: begins 03-08, ends 11-01. Adjacent Mondays shift open by one UTC hour.
    assert calendar.session_open(date(2026, 3, 9)) == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert calendar.session_open(date(2026, 11, 2)) == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_current_session_partitions_a_trading_day(calendar: NyseCalendar) -> None:
    assert calendar.current_session(datetime(2026, 7, 20, 5, 0, tzinfo=NY_SUMMER)) is Session.PRE
    assert (
        calendar.current_session(datetime(2026, 7, 20, 10, 0, tzinfo=NY_SUMMER))
        is Session.REGULAR
    )
    assert (
        calendar.current_session(datetime(2026, 7, 20, 17, 0, tzinfo=NY_SUMMER)) is Session.AFTER
    )
    assert (
        calendar.current_session(datetime(2026, 7, 20, 21, 0, tzinfo=NY_SUMMER)) is Session.CLOSED
    )
    assert calendar.current_session(datetime(2026, 7, 20, 3, 0, tzinfo=NY_SUMMER)) is Session.CLOSED


def test_current_session_is_closed_on_weekend(calendar: NyseCalendar) -> None:
    assert (
        calendar.current_session(datetime(2026, 7, 18, 10, 0, tzinfo=NY_SUMMER)) is Session.CLOSED
    )


def test_is_market_open(calendar: NyseCalendar) -> None:
    assert calendar.is_market_open(datetime(2026, 7, 20, 14, 0, tzinfo=UTC)) is True
    assert calendar.is_market_open(datetime(2026, 7, 20, 12, 0, tzinfo=UTC)) is False


def test_naive_moment_is_rejected(calendar: NyseCalendar) -> None:
    with pytest.raises(ValueError, match="timezone"):
        calendar.is_market_open(datetime(2026, 7, 20, 14, 0))  # noqa: DTZ001
