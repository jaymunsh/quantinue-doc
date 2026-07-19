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
from quantinue.db.domain_records import DailyBarWrite, KnownListing, RawNewsWrite
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.market_data.models import MacroObservation, Provenance, SecuritySnapshot
from quantinue.orchestration.job_factory import (
    JobSources,
    TickerSource,
    build_daily_bars_job,
    build_exit_job,
    build_job_runner,
    build_macro_job,
    build_news_job,
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
        self.sell_signals: dict[str, frozenset[str]] = {}
        self.sell_signal_calls: list[tuple[date, tuple[str, ...]]] = []

    async def bar_coverage(self) -> dict[str, date]:
        return dict(self._coverage)

    async def save_daily_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        self.saved.append(bars)

    async def exit_observations(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyObservation]:
        self.observation_calls.append((trade_date, tickers))
        return dict(self._observations)

    async def approved_sell_profiles(
        self, as_of: date, tickers: tuple[str, ...]
    ) -> dict[str, frozenset[str]]:
        self.sell_signal_calls.append((as_of, tickers))
        return dict(self.sell_signals)


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
        "news",
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
        self.known: dict[str, KnownListing] = {}

    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        self.universes.append(value)

    async def last_known_listings(
        self, tickers: tuple[str, ...]
    ) -> dict[str, KnownListing]:
        return {t: self.known[t] for t in tickers if t in self.known}

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
    job = build_universe_job(
        source=source, domain=domain, held=_held(), config=ScreeningConfig()
    )

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
    job = build_universe_job(
        source=source, domain=domain, held=_held(), config=ScreeningConfig()
    )

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
        source=source,
        domain=domain,
        held=_held(),
        config=ScreeningConfig(universe_size=2),
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["A", "B"]


@pytest.mark.anyio
async def test_listing_feed_members_are_labelled_listed() -> None:
    """이월분과 구분되지 않으면 라벨 자체가 다음 세대의 유령이 된다."""
    # Given
    source = _Screener((_snapshot("AAA", 300),))
    domain = _UniverseDomain()
    job = build_universe_job(
        source=source, domain=domain, held=_held(), config=ScreeningConfig()
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert domain.universes[0].members[0].listing_status == "listed"


@pytest.mark.anyio
async def test_a_holding_that_left_the_listing_feed_is_carried_forward() -> None:
    """상장 피드에서 빠진 보유가 유니버스를 떠나면 청산 경로 전체가 막힌다."""
    # Given
    source = _Screener((_snapshot("AAA", 300),))
    domain = _UniverseDomain(("GONE",))
    domain.known["GONE"] = KnownListing(company_name="Gone Inc", market_cap=42)
    job = build_universe_job(
        source=source, domain=domain, held=_held("GONE"), config=ScreeningConfig()
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    carried = domain.universes[0].members[-1]
    assert carried.ticker == "GONE"
    assert carried.listing_status == "held_delisted"
    # 마지막 관측값을 옮긴다 — 0으로 두면 시총 정렬의 맨 뒤로 가고,
    # 나중에 절단 로직이 바뀌면 문제가 조용히 되돌아온다.
    assert carried.market_cap == 42
    assert carried.company_name == "Gone Inc"
    assert detail == "2 members as of 2026-07-20 (1 held, delisted)"


@pytest.mark.anyio
async def test_a_carried_holding_is_exempt_from_the_universe_size_cap() -> None:
    """캡에 걸려 잘리면 이월의 목적 자체가 사라진다 — 스크리닝의 '보유는 캡 무관'과 같은 원리."""
    # Given
    source = _Screener((_snapshot("A", 3), _snapshot("B", 2), _snapshot("C", 1)))
    domain = _UniverseDomain(("GONE",))
    domain.known["GONE"] = KnownListing(company_name="Gone Inc", market_cap=1)
    job = build_universe_job(
        source=source,
        domain=domain,
        held=_held("GONE"),
        config=ScreeningConfig(universe_size=2),
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["A", "B", "GONE"]


@pytest.mark.anyio
async def test_a_holding_still_in_the_listing_feed_is_not_carried_twice() -> None:
    """정상 보유는 상장분이다 — 이월로 중복되면 PK 충돌과 라벨 거짓이 함께 온다."""
    # Given
    source = _Screener((_snapshot("AAA", 300),))
    domain = _UniverseDomain(("AAA",))
    domain.known["AAA"] = KnownListing(company_name="Stale Inc", market_cap=1)
    job = build_universe_job(
        source=source, domain=domain, held=_held("AAA"), config=ScreeningConfig()
    )

    # When
    detail = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["AAA"]
    assert domain.universes[0].members[0].listing_status == "listed"
    assert detail == "1 members as of 2026-07-20"


@pytest.mark.anyio
async def test_a_holding_never_seen_in_any_universe_is_not_invented() -> None:
    """유니버스에 한 번도 없던 종목은 살 수 없었다 — 이월할 근거도 없다."""
    # Given
    source = _Screener((_snapshot("AAA", 300),))
    domain = _UniverseDomain(("PHANTOM",))
    job = build_universe_job(
        source=source, domain=domain, held=_held("PHANTOM"), config=ScreeningConfig()
    )

    # When
    _ = await job.run(_MONDAY)

    # Then
    assert [m.ticker for m in domain.universes[0].members] == ["AAA"]


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
        "news",
        "screening",
        "exits",
    ]


def test_one_analysis_job_is_registered_per_persona() -> None:
    """원장의 유일성 축이 inv_type이라 페르소나 하나가 잡 하나다.

    한 잡이 두 성향을 함께 돌면 `UNIQUE (ticker, cycle_ts, inv_type)` 위에서
    둘이 서로를 덮어쓰거나, 한 성향이 실패할 때 다른 성향까지 같이 죽는다.
    """
    # Given / When
    runner = build_job_runner(
        _settings(),
        Mvp2Config(),
        store=_Store(_HoldingDomain(())),
        sources=JobSources(analyzer=DeterministicAnalyzer()),
    )

    # Then: 수집 → 스크리닝 → 분석 → 청산. 분석은 범위가 정해진 뒤에만 뜻이 있다.
    assert runner is not None
    names = [job.name for job in runner.jobs]
    assert names[-3:] == ["analysis:aggressive", "analysis:conservative", "exits"]
    assert names.index("screening") < names.index("analysis:aggressive")



class _NewsSource:
    def __init__(self, rows: tuple[RawNewsWrite, ...] = ()) -> None:
        self.rows = rows
        self.calls: list[tuple[date, date]] = []

    async def articles(self, session: date, until: date) -> tuple[RawNewsWrite, ...]:
        self.calls.append((session, until))
        return self.rows


class _NewsDomain(_HoldingDomain):
    def __init__(self) -> None:
        super().__init__(())
        self.saved: list[tuple[RawNewsWrite, ...]] = []

    async def save_raw_news(self, articles: tuple[RawNewsWrite, ...]) -> None:
        self.saved.append(articles)


def _news_row(article_id: int, ticker: str) -> RawNewsWrite:
    return RawNewsWrite(
        article_id=article_id,
        ticker=ticker,
        trade_date=_FRIDAY,
        headline="something happened",
        source="benzinga",
        url=f"https://example.test/{article_id}",
        published_at=datetime(2026, 7, 17, 20, 0, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_the_news_job_asks_from_the_closed_session_through_the_run_day() -> None:
    """직전 세션 이후 주말·당일 프리마켓 기사는 다음 창이 다시 주워주지 않는다."""
    # Given
    source = _NewsSource((_news_row(1, "AAA"),))
    domain = _NewsDomain()

    # When
    job = build_news_job(source=source, domain=domain, calendar=NyseCalendar())
    detail = await job.run(_MONDAY)

    # Then
    assert source.calls == [(_FRIDAY, _MONDAY)]
    assert domain.saved == [(_news_row(1, "AAA"),)]
    assert "1" in detail


@pytest.mark.anyio
async def test_a_day_with_no_news_is_not_an_error() -> None:
    """수집이 비는 날이 있다 — 그걸 실패로 만들면 잡 원장이 거짓말을 한다."""
    # Given
    domain = _NewsDomain()

    # When
    job = build_news_job(source=_NewsSource(), domain=domain, calendar=NyseCalendar())
    detail = await job.run(_MONDAY)

    # Then
    assert domain.saved == [()]
    assert "0" in detail


@pytest.mark.anyio
async def test_todays_approved_sell_signals_reach_the_exit_rules() -> None:
    """3층 soft path의 배선. 판단은 **오늘** 나오고 시세는 **직전 세션** 것이라
    두 날짜가 다르다 — 관측을 조립하는 자리가 여기인 이유다."""
    # Given
    domain = _Domain({"HELD": DailyObservation(last_price=Decimal(100))})
    domain.sell_signals = {"HELD": frozenset({"aggressive"})}
    exit_job = _ExitJob()

    # When
    job = build_exit_job(
        domain=domain,
        exit_job=exit_job,
        tickers=_held("HELD"),
        calendar=NyseCalendar(),
    )
    _ = await job.run(_MONDAY)

    # Then: 시세는 금요일 것을 묻고, 판단은 월요일 것을 묻는다
    assert domain.observation_calls == [(_FRIDAY, ("HELD",))]
    assert domain.sell_signal_calls == [(_MONDAY, ("HELD",))]
    observations = exit_job.calls[0][1]
    assert observations["HELD"].sell_signal_profiles == frozenset({"aggressive"})


@pytest.mark.anyio
async def test_a_sell_signal_on_a_ticker_with_no_observation_is_not_invented() -> None:
    """봉도 하드 이벤트도 없는 종목에 판단만 있다 — 시세 없이 팔 수는 없다."""
    # Given
    domain = _Domain({})
    domain.sell_signals = {"HELD": frozenset({"aggressive"})}
    exit_job = _ExitJob()

    # When
    job = build_exit_job(
        domain=domain,
        exit_job=exit_job,
        tickers=_held("HELD"),
        calendar=NyseCalendar(),
    )
    _ = await job.run(_MONDAY)

    # Then
    assert exit_job.calls[0][1] == {}


class _MacroSource:
    def __init__(self, observations: tuple[object, ...]) -> None:
        self.observations = observations
        self.calls: list[str] = []

    async def macro(self, series: str, execution_id: str) -> tuple[object, ...]:
        self.calls.append(series)
        return self.observations


class _MacroDomain:
    def __init__(self) -> None:
        self.saved: list[object] = []

    async def save_macro(self, value: object) -> None:
        self.saved.append(value)


def _macro_observation(rate: str, observed: datetime) -> object:
    return MacroObservation(
        series="DFF",
        observed_at=observed,
        value=Decimal(rate),
        provenance=Provenance(
            source="macro-feed",
            source_ref="https://fred.test/DFF",
            observed_at=observed,
            captured_at=observed,
            confidence=1.0,
            execution_id="macro:test",
        ),
    )


@pytest.mark.anyio
async def test_the_macro_job_projects_the_latest_rate_into_a_regime_row() -> None:
    """구 러너 role_04가 쓰던 tb_macro 행을 잡이 이어 쓴다 — 구 러너를 지우면
    latest_macro가 읽을 행이 끊기고 risk_off_action이 다시 유령이 되기 때문이다."""
    # Given — 관측 여러 개 중 마지막이 최신이다(role_04와 같은 규약)
    source = _MacroSource(
        (
            _macro_observation("4.00", datetime(2026, 7, 16, tzinfo=UTC)),
            _macro_observation("4.12", datetime(2026, 7, 17, tzinfo=UTC)),
        )
    )
    domain = _MacroDomain()

    # When
    job = build_macro_job(source=source, domain=domain)
    detail = await job.run(_MONDAY)

    # Then — rate/12 산식과 국면 문턱은 role_04와 한 곳을 공유한다
    assert source.calls == ["DFF"]
    saved = domain.saved[0]
    assert saved.rate == pytest.approx(4.12)
    assert saved.risk_score == pytest.approx(4.12 / 12.0)
    assert saved.regime.value == "neutral"
    assert saved.as_of == datetime.combine(_MONDAY, datetime.min.time(), tzinfo=UTC)
    assert "4.12" in detail


@pytest.mark.anyio
async def test_a_day_without_macro_observations_saves_nothing() -> None:
    """관측이 없으면 지어내지 않는다 — 없는 국면을 중립으로 저장하면 그것이
    이틀 동안 '신선한 관측'으로 판단에 들어간다."""
    # Given
    domain = _MacroDomain()

    # When
    job = build_macro_job(source=_MacroSource(()), domain=domain)
    detail = await job.run(_MONDAY)

    # Then
    assert domain.saved == []
    assert "no observations" in detail
