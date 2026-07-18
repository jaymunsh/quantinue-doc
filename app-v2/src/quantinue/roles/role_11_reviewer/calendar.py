"""US equity trading dates and close instants used by role 11."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from typing_extensions import override

_NEW_YORK = ZoneInfo("America/New_York")
_DECEMBER = 12
_WEEKDAYS = 5
_JUNETEENTH_MARKET_START = 2022
_SATURDAY = 5
_SUNDAY = 6


@dataclass(frozen=True, slots=True)
class InvalidTradingOffsetError(ValueError):
    """A calendar offset is outside the supported forward range."""

    trading_days: int

    @override
    def __str__(self) -> str:
        """Describe the invalid offset."""
        return f"trading_days must be positive, got {self.trading_days}"


class Clock(Protocol):
    """Injectable source of aware UTC time."""

    def now(self) -> datetime:
        """Return the current instant."""
        ...


class TradingCalendar(Protocol):
    """Calendar capability consumed by validation and scheduling."""

    def offset(self, start: date, *, trading_days: int) -> date:
        """Return a future trading session."""
        ...

    def session_close(self, session_date: date) -> datetime:
        """Return an aware UTC close instant."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production wall clock."""

    def now(self) -> datetime:
        """Return the current UTC instant."""
        return datetime.now(UTC)


def _observed(day: date) -> date:
    weekday = day.weekday()
    if weekday == _SATURDAY:
        return day - timedelta(days=1)
    if weekday == _SUNDAY:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + (month == _DECEMBER), month % _DECEMBER + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Compute Gregorian Easter using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


@dataclass(frozen=True, slots=True)
class UsEquityTradingCalendar:
    """Regular NYSE sessions required by the first MVP."""

    def holidays(self, year: int) -> frozenset[date]:
        """Return regular full-day US equity market holidays."""
        days = {
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _easter_sunday(year) - timedelta(days=2),
            _last_weekday(year, 5, 0),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25)),
        }
        if year >= _JUNETEENTH_MARKET_START:
            days.add(_observed(date(year, 6, 19)))
        return frozenset(days)

    def is_trading_day(self, day: date) -> bool:
        """Return whether a regular trading session exists on the date."""
        adjacent_holidays = self.holidays(day.year - 1) | self.holidays(day.year)
        adjacent_holidays |= self.holidays(day.year + 1)
        return day.weekday() < _WEEKDAYS and day not in adjacent_holidays

    def offset(self, start: date, *, trading_days: int) -> date:
        """Move forward by an exact positive count of trading sessions."""
        if trading_days < 1:
            raise InvalidTradingOffsetError(trading_days)
        candidate = start
        remaining = trading_days
        while remaining:
            candidate += timedelta(days=1)
            if self.is_trading_day(candidate):
                remaining -= 1
        return candidate

    def session_close(self, session_date: date) -> datetime:
        """Return the regular 16:00 New York close converted to UTC."""
        local_close = datetime.combine(session_date, time(16), tzinfo=_NEW_YORK)
        return local_close.astimezone(UTC)
