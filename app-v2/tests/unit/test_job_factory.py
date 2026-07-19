"""The two Phase 2 jobs: collect the closed session's bars, then act on them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from quantinue.core.config import Settings
from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import DailyBarWrite
from quantinue.orchestration.job_factory import (
    build_daily_bars_job,
    build_exit_job,
    build_job_runner,
)
from quantinue.orchestration.policy import Mvp2Config
from quantinue.roles.exits import DailyObservation

if TYPE_CHECKING:
    from collections.abc import Mapping

# 2026-07-20 월요일 — 직전 세션은 금요일 07-17
_MONDAY = date(2026, 7, 20)
_FRIDAY = date(2026, 7, 17)


def _bar(ticker: str) -> DailyBarWrite:
    return DailyBarWrite(
        trade_date=_FRIDAY,
        ticker=ticker,
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(95),
        close=Decimal(105),
        volume=1_000,
        source="test",
    )


class _Source:
    def __init__(self, bars: tuple[DailyBarWrite, ...]) -> None:
        self.bars = bars
        self.calls: list[tuple[date, tuple[str, ...]]] = []

    async def daily_bars(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        self.calls.append((trade_date, tickers))
        return self.bars


class _Domain:
    def __init__(self, observations: Mapping[str, DailyObservation] | None = None) -> None:
        self.saved: list[tuple[DailyBarWrite, ...]] = []
        self._observations = dict(observations or {})
        self.observation_calls: list[tuple[date, tuple[str, ...]]] = []

    async def save_daily_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        self.saved.append(bars)

    async def exit_observations(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyObservation]:
        self.observation_calls.append((trade_date, tickers))
        return dict(self._observations)


class _ExitJob:
    def __init__(self) -> None:
        self.calls: list[tuple[date, Mapping[str, DailyObservation]]] = []

    async def run(
        self, *, as_of: date, observations: Mapping[str, DailyObservation]
    ) -> tuple[str, ...]:
        self.calls.append((as_of, observations))
        return ("CLOSED",)


async def _held(*tickers: str) -> tuple[str, ...]:
    return tickers


@pytest.mark.anyio
async def test_the_bars_job_collects_the_last_closed_session_not_today() -> None:
    """잡은 개장 전에 돈다 — 오늘 봉은 아직 없다."""
    # Given
    source = _Source((_bar("AAA"),))
    domain = _Domain()
    job = build_daily_bars_job(
        source=source,
        domain=domain,
        tickers=lambda: _held("AAA"),
        calendar=NyseCalendar(),
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    assert source.calls == [(_FRIDAY, ("AAA",))]
    assert domain.saved == [(_bar("AAA"),)]
    assert detail is not None
    assert "1" in detail


@pytest.mark.anyio
async def test_the_bars_job_does_nothing_when_nothing_is_held() -> None:
    # Given
    source = _Source(())
    domain = _Domain()
    job = build_daily_bars_job(
        source=source, domain=domain, tickers=_held, calendar=NyseCalendar()
    )

    # When
    detail = await job.run(_MONDAY)

    # Then: 빈 요청으로 외부 API를 두드리지 않는다
    assert source.calls == []
    assert domain.saved == []
    assert detail == "no tickers"


@pytest.mark.anyio
async def test_the_exit_job_reads_observations_from_the_closed_session() -> None:
    """청산 판정의 입력은 손으로 만든 관측이 아니라 원장에 앉은 일봉이다."""
    # Given
    observation = DailyObservation(last_price=Decimal(105))
    domain = _Domain({"AAA": observation})
    inner = _ExitJob()
    job = build_exit_job(
        domain=domain, exit_job=inner, tickers=lambda: _held("AAA"), calendar=NyseCalendar()
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    assert domain.observation_calls == [(_FRIDAY, ("AAA",))]
    assert inner.calls == [(_MONDAY, {"AAA": observation})]
    assert detail is not None
    assert "1" in detail


@pytest.mark.anyio
async def test_the_exit_job_is_a_no_op_without_holdings() -> None:
    # Given
    domain = _Domain()
    inner = _ExitJob()
    job = build_exit_job(
        domain=domain, exit_job=inner, tickers=_held, calendar=NyseCalendar()
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    assert inner.calls == []
    assert detail == "no holdings"


@pytest.mark.anyio
async def test_the_exit_job_decides_on_today_while_observing_the_closed_session() -> None:
    """보유일 계산(시간 청산)은 오늘 기준, 시세는 직전 세션 기준이다."""
    # Given
    domain = _Domain({"AAA": DailyObservation()})
    inner = _ExitJob()
    job = build_exit_job(
        domain=domain, exit_job=inner, tickers=lambda: _held("AAA"), calendar=NyseCalendar()
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert inner.calls[0][0] == _MONDAY
    assert domain.observation_calls[0][0] == _FRIDAY


class _Store:
    """Postgres-shaped store: the ledger and the readers hang off .domain."""

    def __init__(self, domain: object) -> None:
        self.domain = domain


class _HoldingDomain(_Domain):
    """A domain that also reports open positions, like the real one."""

    def __init__(self, tickers: tuple[str, ...]) -> None:
        super().__init__()
        self._tickers = tickers

    async def open_positions(self) -> tuple[object, ...]:
        return tuple(SimpleNamespace(ticker=ticker) for ticker in self._tickers)


def _settings(*, key: str = "", secret: str = "") -> Settings:
    return Settings(alpaca_api_key=SecretStr(key), alpaca_secret_key=SecretStr(secret))


def test_a_store_without_a_ledger_gets_no_runner() -> None:
    """메모리 스토어에는 tb_job_run이 없다 — 잡을 돌릴 근거가 없다."""
    assert build_job_runner(_settings(), Mvp2Config(), store=object()) is None


def test_without_alpaca_credentials_only_the_exit_job_is_registered() -> None:
    """시세를 못 받아도 청산은 돌아야 한다 — 저장된 봉으로 판단할 수 있다."""
    # Given
    store = _Store(_HoldingDomain(("AAA",)))

    # When
    runner = build_job_runner(_settings(), Mvp2Config(), store=store)

    # Then
    assert runner is not None
    assert [job.name for job in runner.jobs] == ["exits"]


def test_with_credentials_collection_is_registered_before_the_exit_job() -> None:
    """순서가 계약이다 — 오늘 봉을 받기 전에 청산하면 어제 시세로 판다."""
    # Given
    store = _Store(_HoldingDomain(("AAA",)))

    # When
    runner = build_job_runner(
        _settings(key="k", secret="s"), Mvp2Config(), store=store
    )

    # Then
    assert runner is not None
    assert [job.name for job in runner.jobs] == ["daily_bars", "exits"]


@pytest.mark.anyio
async def test_the_registered_exit_job_reads_holdings_from_the_store() -> None:
    """보유 종목은 손으로 넘기는 게 아니라 원장에서 온다 — 중복은 접어서."""
    # Given
    domain = _HoldingDomain(("AAA", "BBB", "AAA"))
    runner = build_job_runner(_settings(), Mvp2Config(), store=_Store(domain))

    # When
    assert runner is not None
    _ = await runner.jobs[0].run(_MONDAY)

    # Then
    assert domain.observation_calls == [(_FRIDAY, ("AAA", "BBB"))]
