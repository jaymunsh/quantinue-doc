"""Compose the Phase 2 jobs out of the pieces that already exist.

여기 있는 것은 배선뿐이고 판단은 하나도 없다. 수집기(``AlpacaBarSource``)와
청산 잡(``ExitJob``)은 각자 완성돼 있었지만 둘을 부르는 코드가 없어서 프로덕션
경로에서는 아무도 돌지 않았다 — 이 모듈이 그 사이를 잇는다.

두 잡 모두 **직전에 닫힌 세션**을 본다. 잡은 보통 개장 전에 도는데 그 시점의
오늘 봉은 존재하지 않기 때문이다. 다만 청산 판정 자체는 오늘 날짜로 한다 —
보유일(시간 청산)은 "오늘까지 며칠 들고 있었나"이지 "어제까지"가 아니다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias

from quantinue.broker.mock import MockBroker
from quantinue.core.market_calendar import NyseCalendar
from quantinue.market_data.alpaca_bars import AlpacaBarSource
from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.roles.exits.job import ExitJob

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from quantinue.core.config import Settings
    from quantinue.db.domain_records import DailyBarWrite
    from quantinue.orchestration.policy import Mvp2Config
    from quantinue.roles.exits import DailyObservation

TickerSource: TypeAlias = Callable[[], Awaitable[tuple[str, ...]]]


class _BarSource(Protocol):
    async def daily_bars(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        """Fetch one session's bars for the requested tickers."""
        ...


class _BarSink(Protocol):
    async def save_daily_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        """Upsert the collected bars into the ledger."""
        ...


class _ObservationSource(Protocol):
    async def exit_observations(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyObservation]:
        """Project stored bars into what the exit rules consume."""
        ...


class _ExitRunner(Protocol):
    async def run(
        self, *, as_of: date, observations: Mapping[str, DailyObservation]
    ) -> tuple[object, ...]:
        """Apply the exit rules to every open position."""
        ...


def build_daily_bars_job(
    *,
    source: _BarSource,
    domain: _BarSink,
    tickers: TickerSource,
    calendar: NyseCalendar,
    name: str = "daily_bars",
) -> JobDefinition:
    """Collect and store the last closed session's bars."""

    async def run(as_of: date) -> str:
        wanted = await tickers()
        if not wanted:
            # 빈 목록으로 외부 API를 두드리지 않는다 — 한도만 축낸다.
            return "no tickers"
        session = calendar.previous_trading_day(as_of)
        bars = await source.daily_bars(session, wanted)
        await domain.save_daily_bars(bars)
        return f"{len(bars)}/{len(wanted)} bars for {session.isoformat()}"

    return JobDefinition(name=name, run=run)


def build_exit_job(
    *,
    domain: _ObservationSource,
    exit_job: _ExitRunner,
    tickers: TickerSource,
    calendar: NyseCalendar,
    name: str = "exits",
) -> JobDefinition:
    """Apply the exit rules to the holdings, using the stored bars as evidence."""

    async def run(as_of: date) -> str:
        held = await tickers()
        if not held:
            return "no holdings"
        session = calendar.previous_trading_day(as_of)
        observations = await domain.exit_observations(session, held)
        closed = await exit_job.run(as_of=as_of, observations=observations)
        return f"{len(closed)}/{len(held)} closed"

    return JobDefinition(name=name, run=run)


def build_job_runner(
    settings: Settings, config: Mvp2Config, *, store: object
) -> JobRunner | None:
    """Assemble the background job runner for this application, if it can run.

    잡 등록 **순서가 계약이다**. 수집이 청산보다 먼저 와야 한다 — 오늘 봉을
    받기 전에 청산하면 하루 묵은 시세로 파는 셈이 된다. 한 틱 안에서 순서대로
    돌기 때문에 이 순서가 그대로 데이터 의존성을 만족시킨다.
    """
    domain = getattr(store, "domain", None)
    if domain is None:
        # 메모리 스토어에는 tb_job_run이 없다 — 멱등의 근거가 없으면 안 돈다.
        return None
    calendar = NyseCalendar()

    async def held_tickers() -> tuple[str, ...]:
        positions = await domain.open_positions()
        # dict.fromkeys: 같은 종목을 여러 계좌가 들고 있어도 시세는 한 번만 받는다.
        return tuple(dict.fromkeys(position.ticker for position in positions))

    jobs: list[JobDefinition] = []
    key = settings.alpaca_api_key.get_secret_value().strip()
    secret = settings.alpaca_secret_key.get_secret_value().strip()
    if key and secret:
        jobs.append(
            build_daily_bars_job(
                source=AlpacaBarSource(
                    key_id=key,
                    secret_key=secret,
                    symbols_per_request=config.market_data.symbols_per_request,
                ),
                domain=domain,
                tickers=held_tickers,
                calendar=calendar,
            )
        )
    # 자격증명이 없어도 청산은 등록한다. 이미 저장된 봉만으로도 판단할 수 있고,
    # 관측이 없는 종목은 ExitJob이 알아서 건너뛴다 — 수집 실패가 매도로
    # 둔갑하지 않는다.
    jobs.append(
        build_exit_job(
            domain=domain,
            exit_job=ExitJob(
                store=store,
                broker=MockBroker(),
                time_exit_bdays=config.exits.time_exit_bdays,
                calendar=calendar,
            ),
            tickers=held_tickers,
            calendar=calendar,
        )
    )
    return JobRunner(
        config=config.jobs, ledger=domain, jobs=tuple(jobs), calendar=calendar
    )
