"""NYSE (XNYS) trading calendar adapter with extended-hours session windows."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum, unique
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

NEW_YORK = ZoneInfo("America/New_York")
PRE_SESSION_START = time(4, 0)
AFTER_SESSION_END = time(20, 0)


@unique
class Session(StrEnum):
    """Where a moment falls inside one New York trading day."""

    PRE = "pre"
    REGULAR = "regular"
    AFTER = "after"
    CLOSED = "closed"


def _require_timezone(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        msg = "moment must include a timezone"
        raise ValueError(msg)
    return moment


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


class NyseCalendar:
    """Answer trading-day, session-boundary, and business-day questions."""

    def __init__(self) -> None:
        """Bind the process-cached XNYS calendar."""
        self._calendar = _xnys()

    def is_trading_day(self, day: date) -> bool:
        """Return whether the exchange holds a session on this date."""
        return bool(self._calendar.is_session(day.isoformat()))

    def session_open(self, day: date) -> datetime:
        """Return the regular-session open in UTC; reject non-trading days."""
        if not self.is_trading_day(day):
            msg = f"{day.isoformat()} is not a trading day"
            raise ValueError(msg)
        return self._calendar.session_open(day.isoformat()).to_pydatetime().astimezone(UTC)

    def session_close(self, day: date) -> datetime:
        """Return the regular-session close in UTC; reject non-trading days."""
        if not self.is_trading_day(day):
            msg = f"{day.isoformat()} is not a trading day"
            raise ValueError(msg)
        return self._calendar.session_close(day.isoformat()).to_pydatetime().astimezone(UTC)

    def add_business_days(self, day: date, count: int) -> date:
        """Advance count sessions past the given date (T+N settlement math)."""
        if count < 0:
            msg = "count must not be negative"
            raise ValueError(msg)
        current = day
        remaining = count
        while remaining > 0 or not self.is_trading_day(current):
            current += timedelta(days=1)
            if self.is_trading_day(current):
                remaining -= 1
        return current

    def previous_trading_day(self, day: date) -> date:
        """Return the last session that closed strictly before the given date.

        일간 잡은 "오늘"이 아니라 **마지막으로 닫힌 세션**을 다룬다. 잡은 보통
        개장 전에 도는데 그때 오늘 봉은 아직 존재하지 않기 때문이다 — 오늘로
        물으면 매일 빈손으로 돌아온다.
        """
        current = day - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current

    def is_market_open(self, moment: datetime) -> bool:
        """Return whether the regular session is open at this moment."""
        return self.current_session(moment) is Session.REGULAR

    def current_session(self, moment: datetime) -> Session:
        """Partition a moment into pre/regular/after/closed (America/New_York)."""
        local = _require_timezone(moment).astimezone(NEW_YORK)
        day = local.date()
        if not self.is_trading_day(day):
            return Session.CLOSED
        opened = self.session_open(day).astimezone(NEW_YORK)
        closed = self.session_close(day).astimezone(NEW_YORK)
        if opened <= local < closed:
            return Session.REGULAR
        if local < opened and local.time() >= PRE_SESSION_START:
            return Session.PRE
        if local >= closed and local.time() < AFTER_SESSION_END:
            return Session.AFTER
        return Session.CLOSED
