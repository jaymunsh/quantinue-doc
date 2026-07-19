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
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Protocol, TypeAlias

from quantinue.broker.mock import MockBroker
from quantinue.core.market_calendar import NyseCalendar
from quantinue.market_data.alpaca_bars import AlpacaBarSource
from quantinue.market_data.sec_daily_index import SecDailyIndexSource
from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.roles.exits.job import ExitJob
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quantinue.core.config import Settings
    from quantinue.db.domain_records import DailyBarWrite, RawDisclosureWrite
    from quantinue.market_data.models import SecuritySnapshot
    from quantinue.orchestration.policy import Mvp2Config, ScreeningConfig
    from quantinue.roles.exits import DailyObservation

TickerSource: TypeAlias = Callable[[date], Awaitable[tuple[str, ...]]]


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


class _UniverseSource(Protocol):
    async def screener(self, execution_id: str) -> tuple[SecuritySnapshot, ...]:
        """Fetch the raw listing feed."""
        ...


class _UniverseSink(Protocol):
    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        """Upsert one universe snapshot."""
        ...


def build_universe_job(
    *,
    source: _UniverseSource,
    domain: _UniverseSink,
    config: ScreeningConfig,
    name: str = "universe",
) -> JobDefinition:
    """Rebuild the tradable universe snapshot for this period.

    선언은 원래 주간이었지만(``contracts.py`` docstring) 코드는 매 런 다시
    받고 있었다 — 상장 목록은 하루 단위로 의미 있게 변하지 않으므로 매일
    2000행 파티션을 새로 쓰는 것은 비용만 늘리고 정보는 안 늘린다. 이제 주기는
    ``jobs.cadences.universe``가 소유한다.

    ``as_of_date``는 잡이 돈 날이고, 그게 곧 "이번 주 유니버스"를 가리키는
    열쇠가 된다 — 소비자는 오늘 날짜가 아니라 **최신 스냅샷 날짜**를 찾아야
    한다.
    """

    async def run(as_of: date) -> str:
        snapshots = await source.screener(f"universe:{as_of.isoformat()}")
        # 시총 내림차순 — 잘릴 때 남는 것이 임의의 조각이 아니라 큰 이름들이어야
        # 한다. 소스의 정렬 순서는 아무 의미가 없다.
        ranked = sorted(
            (item for item in snapshots if item.market_cap > 0),
            key=lambda item: (-item.market_cap, item.ticker),
        )[: config.universe_size]
        if not ranked:
            msg = "screener returned no eligible securities"
            raise ValueError(msg)
        await domain.save_universe(
            UniverseScreenerOutput(
                run_id=f"universe:{as_of.isoformat()}",
                generated_at=datetime.combine(as_of, time(), tzinfo=UTC),
                members=tuple(
                    UniverseMember(
                        as_of_date=as_of,
                        ticker=item.ticker,
                        company_name=item.name,
                        market_cap=int(item.market_cap),
                        evidence_ids=(f"universe:{as_of.isoformat()}:{item.ticker}",),
                    )
                    for item in ranked
                ),
            )
        )
        return f"{len(ranked)} members as of {as_of.isoformat()}"

    return JobDefinition(name=name, run=run)


class _FilingSource(Protocol):
    async def filings(self, trade_date: date) -> tuple[RawDisclosureWrite, ...]:
        """Fetch one session's whole-market filings."""
        ...


class _FilingSink(Protocol):
    async def save_raw_disclosures(
        self, filings: tuple[RawDisclosureWrite, ...]
    ) -> None:
        """Upsert the collected filings into the raw ledger."""
        ...


def build_disclosures_job(
    *,
    source: _FilingSource,
    domain: _FilingSink,
    calendar: NyseCalendar,
    name: str = "disclosures",
) -> JobDefinition:
    """Collect the last closed session's filings for the whole market.

    종목 목록을 받지 않는 유일한 수집 잡이다 — 인덱스가 그날 전부를 주므로
    누구를 볼지 미리 정할 필요가 없다. 그래서 스크리너에서 탈락한 보유 종목의
    상장폐지 공시도 자연히 걸린다. 그게 이 잡의 존재 이유다.

    거래일만 묻는 책임이 여기 있다: SEC는 없는 인덱스에 403을 주는데 그건
    User-Agent 정책 차단과 구분되지 않아서, 어댑터는 아무것도 삼키지 않는다.
    """

    async def run(as_of: date) -> str:
        session = calendar.previous_trading_day(as_of)
        filings = await source.filings(session)
        await domain.save_raw_disclosures(filings)
        hard = sum(1 for filing in filings if filing.is_hard_event)
        return f"{len(filings)} filings ({hard} hard) for {session.isoformat()}"

    return JobDefinition(name=name, run=run)


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
        wanted = await tickers(as_of)
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
        held = await tickers(as_of)
        if not held:
            return "no holdings"
        session = calendar.previous_trading_day(as_of)
        observations = await domain.exit_observations(session, held)
        closed = await exit_job.run(as_of=as_of, observations=observations)
        return f"{len(closed)}/{len(held)} closed"

    return JobDefinition(name=name, run=run)


@dataclass(frozen=True, slots=True)
class JobSources:
    """The collection adapters, injectable so tests never touch the network.

    한 묶음으로 둔 이유: 잡이 늘 때마다 ``build_job_runner``의 인자가 늘면
    호출부가 매번 바뀐다. 여기 None인 항목은 그 잡을 등록하지 않는다는 뜻이고,
    ``disclosures``만 기본 어댑터로 채워진다 — SEC는 자격증명이 없어서
    항상 쓸 수 있기 때문이다.
    """

    market_data: _UniverseSource | None = None
    bars: _BarSource | None = None
    disclosures: _FilingSource | None = None


def build_job_runner(
    settings: Settings,
    config: Mvp2Config,
    *,
    store: object,
    sources: JobSources | None = None,
) -> JobRunner | None:
    """Assemble the background job runner for this application, if it can run.

    잡 등록 **순서가 계약이다**: 유니버스 → 일봉 → 공시 → 청산. 수집이 청산보다
    먼저 와야 오늘 시세·공시로 판단하고, 유니버스가 일봉보다 먼저 와야 그 주에
    새로 든 종목이 봉 없이 남지 않는다. 한 틱 안에서 순서대로 돌기 때문에 이
    순서가 그대로 데이터 의존성을 만족시킨다.
    """
    selected = sources or JobSources()
    domain = getattr(store, "domain", None)
    if domain is None:
        # 메모리 스토어에는 tb_job_run이 없다 — 멱등의 근거가 없으면 안 돈다.
        return None
    calendar = NyseCalendar()

    async def held_tickers(_: date) -> tuple[str, ...]:
        """Tickers we actually own — the only ones the exit rules can act on."""
        positions = await domain.open_positions()
        # dict.fromkeys: 같은 종목을 여러 계좌가 들고 있어도 한 번만 본다.
        return tuple(dict.fromkeys(position.ticker for position in positions))

    async def covered_tickers(as_of: date) -> tuple[str, ...]:
        """Holdings first, then the universe snapshot the universe job produced."""
        held = list(await held_tickers(as_of))
        # 유니버스도 덮는다 — 스크리닝(Phase 3)은 봉이 있어야 API 0콜로 랭킹을
        # 짤 수 있고, 봉이 없으면 500 캡이 다시 살아난다. 보유를 앞에 두는
        # 이유는 상한에 걸려 잘릴 때 보유가 먼저 잘리면 안 되기 때문이다.
        #
        # 스냅샷 날짜를 tb_universe의 최대 as_of_date가 아니라 **잡 원장**에서
        # 가져오는 이유: 구 11단계 러너의 role_01도 같은 테이블에 오늘 날짜로
        # 쓰는데, fixture 모드에서는 그게 1행짜리다(D6 점진 교체 중이라 둘이
        # 공존한다). 최대 날짜를 믿으면 그 1행이 주간 2000행 스냅샷을 통째로
        # 가린다 — 실제로 스모크에서 그렇게 됐다.
        snapshot = await domain.last_job_success("universe")
        universe = list(await domain.universe_tickers(snapshot)) if snapshot else []
        return tuple(dict.fromkeys([*held, *universe]))

    jobs: list[JobDefinition] = []
    key = settings.alpaca_api_key.get_secret_value().strip()
    secret = settings.alpaca_secret_key.get_secret_value().strip()
    if selected.market_data is not None:
        jobs.append(
            build_universe_job(
                source=selected.market_data, domain=domain, config=config.screening
            )
        )
    bar_source = selected.bars
    if bar_source is None and key and secret:
        bar_source = AlpacaBarSource(
            key_id=key,
            secret_key=secret,
            symbols_per_request=config.market_data.symbols_per_request,
        )
    if bar_source is not None:
        jobs.append(
            build_daily_bars_job(
                source=bar_source,
                domain=domain,
                tickers=covered_tickers,
                calendar=calendar,
            )
        )
    jobs.append(
        build_disclosures_job(
            source=selected.disclosures or SecDailyIndexSource(),
            domain=domain,
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
            # 청산은 보유만 본다. 유니버스까지 넘기면 2000종목 관측을 읽고
            # 하나도 쓰지 않는다 — 청산이 할 수 있는 일은 파는 것뿐이다.
            tickers=held_tickers,
            calendar=calendar,
        )
    )
    return JobRunner(
        config=config.jobs, ledger=domain, jobs=tuple(jobs), calendar=calendar
    )
