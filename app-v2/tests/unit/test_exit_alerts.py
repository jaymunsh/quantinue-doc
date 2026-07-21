"""The defence-line alert: money moved, so a human hears about it that minute."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from quantinue.core.market_calendar import NyseCalendar
from quantinue.orchestration.job_factory import build_exit_job
from quantinue.roles.exits.contracts import (
    DailyObservation,
    ExitDecision,
    ExitReason,
    OpenPosition,
)

_DAY = date(2026, 7, 21)


def _position(ticker: str, quantity: int) -> OpenPosition:
    return OpenPosition(
        order_id=1,
        signal_id=1,
        account_id=1,
        ticker=ticker,
        quantity=quantity,
        entry_price=Decimal("100.00"),
        filled_on=date(2026, 7, 10),
        stop_price=Decimal("85.00"),
        take_profit_price=Decimal("120.00"),
    )


class _Domain:
    async def exit_observations(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyObservation]:
        return {ticker: DailyObservation() for ticker in tickers}

    async def approved_sell_profiles(
        self, as_of: date, tickers: tuple[str, ...]
    ) -> dict[str, frozenset[str]]:
        return {}


class _ExitRunner:
    def __init__(self, decisions: tuple[ExitDecision, ...]) -> None:
        self._decisions = decisions

    async def run(
        self, *, as_of: date, observations: object
    ) -> tuple[ExitDecision, ...]:
        return self._decisions


class _Notify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, message: str) -> None:
        self.messages.append(message)


async def _held(as_of: date) -> tuple[str, ...]:
    return ("NVDA", "OKTA")


@pytest.mark.anyio
async def test_a_closed_position_is_announced_with_its_reason() -> None:
    notify = _Notify()
    decisions = (
        ExitDecision(_position("NVDA", 50), ExitReason.STOP, Decimal("102.10")),
        ExitDecision(_position("OKTA", 200), ExitReason.TIME, Decimal("149.32")),
    )
    job = build_exit_job(
        domain=_Domain(),
        exit_job=_ExitRunner(decisions),
        tickers=_held,
        calendar=NyseCalendar(),
        notify=notify,
    )

    detail = await job.run(_DAY)

    assert detail == "2/2 closed"
    assert len(notify.messages) == 1
    message = notify.messages[0]
    assert "NVDA 50주" in message
    assert "손절" in message
    assert "OKTA 200주" in message
    assert "시간 청산" in message
    assert "$102.10" in message


@pytest.mark.anyio
async def test_a_quiet_day_sends_nothing() -> None:
    """청산 0건은 정상 상태다 — 정상을 매일 알리면 진짜 발동이 묻힌다."""
    notify = _Notify()
    job = build_exit_job(
        domain=_Domain(),
        exit_job=_ExitRunner(()),
        tickers=_held,
        calendar=NyseCalendar(),
        notify=notify,
    )

    _ = await job.run(_DAY)

    assert notify.messages == []


@pytest.mark.anyio
async def test_without_a_notifier_the_job_still_closes_positions() -> None:
    decisions = (ExitDecision(_position("NVDA", 50), ExitReason.STOP, Decimal("102.10")),)
    job = build_exit_job(
        domain=_Domain(),
        exit_job=_ExitRunner(decisions),
        tickers=_held,
        calendar=NyseCalendar(),
        notify=None,
    )

    assert await job.run(_DAY) == "1/2 closed"
