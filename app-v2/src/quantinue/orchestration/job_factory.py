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
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from quantinue.broker.mock import MockBroker
from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import DailyPickWrite
from quantinue.market_data.alpaca_bars import AlpacaBarSource
from quantinue.market_data.sec_daily_index import SecDailyIndexSource
from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.roles.exits.job import ExitJob
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)
from quantinue.roles.screening import select_scope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quantinue.core.config import Settings
    from quantinue.db.domain_records import DailyBarWrite, RawDisclosureWrite
    from quantinue.market_data.models import SecuritySnapshot
    from quantinue.orchestration.policy import Mvp2Config, ScreeningConfig
    from quantinue.roles.exits import DailyObservation
    from quantinue.roles.screening import RankedCandidate

TickerSource: TypeAlias = Callable[[date], Awaitable[tuple[str, ...]]]

_ONE_DAY: Final = timedelta(days=1)


class _BarSource(Protocol):
    async def daily_bars_range(
        self, start: date, end: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        """Fetch an inclusive window of bars for the requested tickers."""
        ...


class _BarSink(Protocol):
    async def bar_coverage(self) -> dict[str, date]:
        """Return the newest stored bar date per ticker."""
        ...

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


def build_daily_bars_job(  # noqa: PLR0913 - 각 인자가 교체 가능한 협력자 하나다
    *,
    source: _BarSource,
    domain: _BarSink,
    tickers: TickerSource,
    calendar: NyseCalendar,
    history_days: int,
    name: str = "daily_bars",
) -> JobDefinition:
    """Fill the ledger up to the last closed session, backfilling what is missing.

    **백필을 별도 잡으로 만들지 않은 이유.** 스크리닝의 랭킹 지표는 전부 창
    지표(``ret_20d``·``ma20/50``·``high_252_ratio``·``rsi``)라 하루치 봉으로는
    계산 자체가 안 된다. 그렇다고 "처음 한 번만 도는 잡"을 두면, 그 잡이 실패한
    날과 성공한 날의 시스템이 서로 다른 물건이 된다. 대신 이 잡이 매번 **원장이
    아는 마지막 날부터 직전 세션까지**를 채우게 하면 첫 실행은 백필이고 이후
    실행은 증분이라, 경로가 하나뿐이고 중단된 백필도 다음 실행이 이어받는다.

    창을 두 갈래로 나눠 부른다:
    - 봉이 하나도 없는 종목: ``history_days`` 전체 창. 증분만 주면 창 지표가
      영원히 계산되지 않는다.
    - 이미 있는 종목: 원장이 아는 가장 최신 세션 다음 날부터.

    남는 비용 하나: 유니버스에 있지만 거래소에 봉이 **한 번도** 없는 종목(실측
    2000 중 1개)은 매 실행 cold로 남아 전체 창을 다시 묻는다. 응답이 비어 있어
    원장에 아무것도 안 남기 때문이다. "찾아봤지만 없더라"를 기록할 자리를 새로
    만들 만한 비용은 아니라고 판단했다 — 요청 한 건이다.
    """

    async def run(as_of: date) -> str:
        wanted = await tickers(as_of)
        if not wanted:
            # 빈 목록으로 외부 API를 두드리지 않는다 — 한도만 축낸다.
            return "no tickers"
        session = calendar.previous_trading_day(as_of)
        history_start = session - timedelta(days=history_days)
        coverage = await domain.bar_coverage()
        cold = tuple(ticker for ticker in wanted if ticker not in coverage)
        warm = tuple(ticker for ticker in wanted if ticker in coverage)
        collected: tuple[DailyBarWrite, ...] = ()
        if cold:
            collected += await source.daily_bars_range(history_start, session, cold)
        if warm:
            # **가장 앞선** 종목이 창의 시작을 정한다. 뒤처진 종목에 맞추면
            # 실행마다 수십만 행을 다시 받는다(실측: 2회차가 30만 봉 재수신).
            # 뒤처지는 이유는 우리가 못 받아서가 아니라 거래소에 그 종목의 봉이
            # 없어서다 — 상장폐지·거래정지가 대부분이라 소급해도 채워지지 않는다.
            # 정말 아무것도 없는 종목은 cold 갈래가 전체 창으로 덮는다.
            warm_start = max(max(coverage[ticker] for ticker in warm) + _ONE_DAY, history_start)
            if warm_start <= session:
                collected += await source.daily_bars_range(warm_start, session, warm)
        await domain.save_daily_bars(collected)
        return (
            f"{len(collected)} bars up to {session.isoformat()}"
            f" ({len(cold)} backfilled, {len(warm)} incremental)"
        )

    return JobDefinition(name=name, run=run)


class _ScreeningStore(Protocol):
    async def rank_universe(
        self,
        session: date,
        universe_as_of: date,
        *,
        min_price_usd: float,
        min_avg_dollar_vol: float,
        min_history_sessions: int,
    ) -> tuple[RankedCandidate, ...]:
        """Rank the whole universe from stored bars."""
        ...

    async def save_daily_picks(self, picks: tuple[DailyPickWrite, ...]) -> None:
        """Replace the session's analysis scope."""
        ...

    async def last_job_success(self, job_name: str) -> date | None:
        """Return the slot the named job last completed."""
        ...

    async def universe_tickers(self, as_of: date) -> tuple[str, ...]:
        """Return one universe snapshot's tickers."""
        ...


def build_screening_job(
    *,
    domain: _ScreeningStore,
    held: TickerSource,
    config: ScreeningConfig,
    calendar: NyseCalendar,
    name: str = "screening",
) -> JobDefinition:
    """Decide today's analysis scope from stored bars — zero API calls.

    구 role_02·03은 지표를 종목당 1콜로 받았기 때문에 500종목 캡이 필요했고,
    그 캡 밖의 종목은 아무도 보지 않았다. 봉이 원장에 앉은 지금은 전 유니버스를
    한 문장으로 줄 세울 수 있어서 캡이 근거를 잃는다.

    범위는 **상위 llm_depth + 보유 전부**다. 보유가 캡과 무관한 이유는 판단이
    아니라 구조다 — 시그널이 ``tb_daily_pick``을 FK로 참조하므로, 범위 밖인
    보유 종목은 청산 시그널을 남길 자리가 없다.

    유니버스 스냅샷 날짜를 잡 원장에서 가져오는 이유는 일봉 수집 잡과 같다:
    구 러너의 role_01도 같은 테이블에 오늘 날짜로 1행을 쓰기 때문에(D6 점진
    교체 중 공존), 최대 날짜를 믿으면 그 1행이 주간 스냅샷을 통째로 가린다.
    """

    async def run(as_of: date) -> str:
        snapshot = await domain.last_job_success("universe")
        if snapshot is None:
            # 유니버스 없이 고른 "상위 N"은 무엇의 상위인지 알 수 없다.
            return "no universe snapshot"
        session = calendar.previous_trading_day(as_of)
        candidates = await domain.rank_universe(
            session,
            snapshot,
            min_price_usd=config.min_price_usd,
            min_avg_dollar_vol=config.min_avg_dollar_vol,
            min_history_sessions=config.min_history_sessions,
        )
        holdings = await held(as_of)
        # 유니버스에 없는 보유는 픽 행을 만들 수 없다(FK). 조용히 지나가면
        # 상장폐지된 보유를 못 파는 상태가 되므로 요약에 남긴다.
        listed = frozenset(await domain.universe_tickers(snapshot))
        unlisted = tuple(ticker for ticker in holdings if ticker not in listed)
        picks = select_scope(
            candidates,
            held=tuple(ticker for ticker in holdings if ticker in listed),
            depth=config.llm_depth,
        )
        await domain.save_daily_picks(
            tuple(
                DailyPickWrite(
                    trade_date=as_of,
                    ticker=pick.ticker,
                    universe_as_of=snapshot,
                    bucket=pick.bucket.value,
                    rank=pick.rank,
                    # 섹터 데이터를 가진 소스가 아직 없다. 지어내는 대신
                    # 모른다고 적는다 — 구 role_03도 같은 값을 썼다.
                    sector="미분류",
                    score=Decimal(str(pick.score)),
                )
                for pick in picks
            )
        )
        detail = f"{len(picks)} picks from {len(candidates)} ranked for {session.isoformat()}"
        if unlisted:
            detail += f" (skipped {len(unlisted)} unlisted holdings: {','.join(unlisted)})"
        return detail

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

    잡 등록 **순서가 계약이다**: 유니버스 → 일봉 → 공시 → 스크리닝 → 청산. 수집이 청산보다
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
                history_days=config.market_data.history_days,
            )
        )
    jobs.append(
        build_disclosures_job(
            source=selected.disclosures or SecDailyIndexSource(),
            domain=domain,
            calendar=calendar,
        )
    )
    jobs.append(
        build_screening_job(
            domain=domain,
            held=held_tickers,
            config=config.screening,
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
