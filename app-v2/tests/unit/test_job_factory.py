"""The two Phase 2 jobs: collect the closed session's bars, then act on them."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from quantinue.core.config import Settings
from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import DailyBarWrite
from quantinue.market_data.models import Provenance, SecuritySnapshot
from quantinue.orchestration.job_factory import (
    JobSources,
    TickerSource,
    build_daily_bars_job,
    build_exit_job,
    build_job_runner,
    build_universe_job,
)
from quantinue.orchestration.policy import Mvp2Config, ScreeningConfig
from quantinue.roles.exits import DailyObservation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quantinue.roles.role_01_universe_screener.contracts import UniverseScreenerOutput

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
        self.calls: list[tuple[date, date, tuple[str, ...]]] = []

    async def daily_bars_range(
        self, start: date, end: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        self.calls.append((start, end, tickers))
        return self.bars


class _Domain:
    def __init__(
        self,
        observations: Mapping[str, DailyObservation] | None = None,
        coverage: Mapping[str, date] | None = None,
    ) -> None:
        self.saved: list[tuple[DailyBarWrite, ...]] = []
        self._observations = dict(observations or {})
        self._coverage = dict(coverage or {})
        self.observation_calls: list[tuple[date, tuple[str, ...]]] = []

    async def bar_coverage(self) -> dict[str, date]:
        return dict(self._coverage)

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


def _held(*tickers: str) -> TickerSource:
    async def source(as_of: date) -> tuple[str, ...]:
        return tickers

    return source


@pytest.mark.anyio
async def test_the_bars_job_collects_the_last_closed_session_not_today() -> None:
    """잡은 개장 전에 돈다 — 오늘 봉은 아직 없다."""
    # Given
    source = _Source((_bar("AAA"),))
    domain = _Domain()
    job = build_daily_bars_job(
        source=source,
        domain=domain,
        tickers=_held("AAA"),
        calendar=NyseCalendar(),
        history_days=400,
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    # 원장에 봉이 없는 종목이라 창 전체를 소급해 받는다 — 창 지표의 전제다.
    assert source.calls == [(_FRIDAY - timedelta(days=400), _FRIDAY, ("AAA",))]
    assert domain.saved == [(_bar("AAA"),)]
    assert detail is not None
    assert "1" in detail


@pytest.mark.anyio
async def test_the_bars_job_does_nothing_when_nothing_is_held() -> None:
    # Given
    source = _Source(())
    domain = _Domain()
    job = build_daily_bars_job(
        source=source,
        domain=domain,
        tickers=_held(),
        calendar=NyseCalendar(),
        history_days=400,
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
        domain=domain, exit_job=inner, tickers=_held("AAA"), calendar=NyseCalendar()
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
        domain=domain, exit_job=inner, tickers=_held(), calendar=NyseCalendar()
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
        domain=domain, exit_job=inner, tickers=_held("AAA"), calendar=NyseCalendar()
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

    async def last_job_success(self, job_name: str) -> date | None:
        return None

    async def universe_tickers(self, as_of: date) -> tuple[str, ...]:
        return ()


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
    assert [job.name for job in runner.jobs] == ["disclosures", "screening", "exits"]


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
    assert [job.name for job in runner.jobs] == [
        "daily_bars",
        "disclosures",
        "screening",
        "exits",
    ]


@pytest.mark.anyio
async def test_the_registered_exit_job_reads_holdings_from_the_store() -> None:
    """보유 종목은 손으로 넘기는 게 아니라 원장에서 온다 — 중복은 접어서."""
    # Given
    domain = _HoldingDomain(("AAA", "BBB", "AAA"))
    runner = build_job_runner(_settings(), Mvp2Config(), store=_Store(domain))

    # When
    assert runner is not None
    exits = next(job for job in runner.jobs if job.name == "exits")
    _ = await exits.run(_MONDAY)

    # Then
    assert domain.observation_calls == [(_FRIDAY, ("AAA", "BBB"))]


class _Screener:
    """Market data that only answers the universe question."""

    def __init__(self, snapshots: tuple[SecuritySnapshot, ...]) -> None:
        self.snapshots = snapshots
        self.calls: list[str] = []

    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        self.calls.append(execution_id)
        return self.snapshots


class _UniverseDomain(_HoldingDomain):
    def __init__(self, tickers: tuple[str, ...] = ()) -> None:
        super().__init__(tickers)
        self.universes: list[UniverseScreenerOutput] = []
        self.latest: date | None = None
        self.members: dict[date, tuple[str, ...]] = {}

    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        self.universes.append(value)

    async def last_job_success(self, job_name: str) -> date | None:
        return self.latest if job_name == "universe" else None

    async def universe_tickers(self, as_of: date) -> tuple[str, ...]:
        return self.members.get(as_of, ())


def _snapshot(ticker: str, cap: int) -> SecuritySnapshot:
    return SecuritySnapshot(
        ticker=ticker,
        name=f"{ticker} Inc",
        market_cap=Decimal(cap),
        last_price=Decimal(10),
        volume=1_000,
        provenance=Provenance(
            source="test",
            source_ref="test://universe",
            observed_at=datetime(2026, 7, 20, tzinfo=UTC),
            captured_at=datetime(2026, 7, 20, tzinfo=UTC),
            confidence=1.0,
            execution_id="test",
        ),
    )


@pytest.mark.anyio
async def test_the_universe_job_stamps_the_snapshot_with_the_day_it_ran() -> None:
    """주간 스냅샷이므로 as_of_date는 '이번 주 유니버스'를 가리키는 열쇠다."""
    # Given
    source = _Screener((_snapshot("AAA", 300), _snapshot("BBB", 200)))
    domain = _UniverseDomain()
    job = build_universe_job(source=source, domain=domain, config=ScreeningConfig())

    # When
    detail = await job.run(_MONDAY)

    # Then
    assert [m.as_of_date for m in domain.universes[0].members] == [_MONDAY, _MONDAY]
    assert [m.ticker for m in domain.universes[0].members] == ["AAA", "BBB"]
    assert detail == "2 members as of 2026-07-20"


@pytest.mark.anyio
async def test_the_universe_job_ranks_by_market_cap() -> None:
    """잘릴 때 남는 것이 임의의 조각이 아니라 큰 이름들이어야 한다."""
    # Given
    source = _Screener((_snapshot("SMALL", 10), _snapshot("BIG", 900)))
    domain = _UniverseDomain()
    job = build_universe_job(source=source, domain=domain, config=ScreeningConfig())

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["BIG", "SMALL"]


@pytest.mark.anyio
async def test_the_universe_job_honours_the_configured_size() -> None:
    # Given
    source = _Screener((_snapshot("A", 3), _snapshot("B", 2), _snapshot("C", 1)))
    domain = _UniverseDomain()
    job = build_universe_job(
        source=source, domain=domain, config=ScreeningConfig(universe_size=2)
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["A", "B"]


@pytest.mark.anyio
async def test_bars_cover_the_universe_snapshot_as_well_as_holdings() -> None:
    """스크리닝은 봉이 있어야 공짜다 — 보유만 받으면 유니버스 랭킹을 못 짠다."""
    # Given
    domain = _UniverseDomain(("HELD",))
    domain.latest = date(2026, 7, 13)
    domain.members[date(2026, 7, 13)] = ("UNIA", "HELD", "UNIB")
    source = _Source(())
    runner = build_job_runner(
        _settings(), Mvp2Config(), store=_Store(domain), sources=JobSources(bars=source)
    )

    # When
    assert runner is not None
    bars = next(job for job in runner.jobs if job.name == "daily_bars")
    _ = await bars.run(_MONDAY)

    # Then: 보유가 앞, 유니버스가 뒤, 중복은 한 번만
    assert source.calls == [
        (_FRIDAY - timedelta(days=400), _FRIDAY, ("HELD", "UNIA", "UNIB"))
    ]


@pytest.mark.anyio
async def test_the_exit_job_ignores_the_universe_and_looks_only_at_holdings() -> None:
    """청산이 할 수 있는 일은 파는 것뿐이다 — 안 가진 2000종목 관측은 낭비다."""
    # Given
    domain = _UniverseDomain(("HELD",))
    domain.latest = date(2026, 7, 13)
    domain.members[date(2026, 7, 13)] = ("UNIA", "HELD", "UNIB")
    runner = build_job_runner(_settings(), Mvp2Config(), store=_Store(domain))

    # When
    assert runner is not None
    exits = next(job for job in runner.jobs if job.name == "exits")
    _ = await exits.run(_MONDAY)

    # Then
    assert domain.observation_calls == [(_FRIDAY, ("HELD",))]


def test_the_universe_job_is_registered_first() -> None:
    """유니버스 → 일봉 → 청산. 뒤집으면 그 주의 새 종목이 봉 없이 남는다."""
    # Given
    domain = _UniverseDomain(("HELD",))

    # When
    runner = build_job_runner(
        _settings(key="k", secret="s"),
        Mvp2Config(),
        store=_Store(domain),
        sources=JobSources(market_data=_Screener((_snapshot("AAA", 1),))),
    )

    # Then
    assert runner is not None
    # 수집 → 판단 → 청산. 스크리닝이 청산보다 앞인 이유는 구조다 — 보유가
    # 그날의 분석 범위 안에 들어와야 청산 시그널을 남길 자리(FK)가 생긴다.
    assert [job.name for job in runner.jobs] == [
        "universe",
        "daily_bars",
        "disclosures",
        "screening",
        "exits",
    ]
