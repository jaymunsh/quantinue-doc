"""Compose the Phase 2 jobs out of the pieces that already exist.

여기 있는 것은 배선뿐이고 판단은 하나도 없다. 수집기(``AlpacaBarSource``)와
청산 잡(``ExitJob``)은 각자 완성돼 있었지만 둘을 부르는 코드가 없어서 프로덕션
경로에서는 아무도 돌지 않았다 — 이 모듈이 그 사이를 잇는다.

두 잡 모두 **직전에 닫힌 세션**을 본다. 잡은 보통 개장 전에 도는데 그 시점의
오늘 봉은 존재하지 않기 때문이다. 다만 청산 판정 자체는 오늘 날짜로 한다 —
보유일(시간 청산)은 "오늘까지 며칠 들고 있었나"이지 "어제까지"가 아니다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from quantinue.broker.mock import MockBroker
from quantinue.core.config import LlmMode
from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import DailyPickWrite
from quantinue.llm.budget import BudgetedAnalyzer, require_pricing_for
from quantinue.llm.provider import build_llm_analyzer
from quantinue.market_data.alpaca_bars import AlpacaBarSource
from quantinue.market_data.alpaca_news import AlpacaNewsSource
from quantinue.market_data.sec_daily_index import SecDailyIndexSource
from quantinue.market_data.sec_ownership import SecOwnershipSource
from quantinue.market_data.wire_news import WireRssSource, default_wire_feeds
from quantinue.notify.telegram import build_failure_notifier
from quantinue.orchestration.job_runner import JobDefinition, JobRunner
from quantinue.roles.allocation.job import AllocationJob
from quantinue.roles.analysis.job import AnalysisJob
from quantinue.roles.disclosure.job import InsiderScoringJob
from quantinue.roles.exits.job import ExitJob
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)
from quantinue.roles.role_04_macro_analysis.contracts import (
    MVP_BASELINE_DOLLAR,
    MVP_BASELINE_NASDAQ_RET,
    MVP_BASELINE_SP500_RET,
    MVP_BASELINE_VIX,
    MacroAnalysisOutput,
    regime_from_rate,
)
from quantinue.roles.screening import select_scope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quantinue.core.config import Settings
    from quantinue.db.domain_records import (
        DailyBarWrite,
        KnownListing,
        RawDisclosureWrite,
        RawNewsWrite,
    )
    from quantinue.llm.budget import LlmUsageLedger
    from quantinue.llm.provider import LlmAnalyzer
    from quantinue.market_data.models import MacroObservation, SecuritySnapshot
    from quantinue.orchestration.policy import Mvp2Config, ScreeningConfig
    from quantinue.roles.disclosure.insider import InsiderPolicy
    from quantinue.roles.exits import DailyObservation
    from quantinue.roles.screening import RankedCandidate


def build_budgeted_analyzer(
    settings: Settings,
    config: Mvp2Config,
    *,
    ledger: LlmUsageLedger | None,
    inner: LlmAnalyzer | None = None,
) -> LlmAnalyzer:
    """Wrap the provider analyzer in the day's spend ledger and ceiling.

    로컬·mock까지 감싸는 것이 의도다. 공짜 모드에서도 콜 수와 토큰이 원장에
    남아야 **전환 전에** 비용을 예측할 수 있고, openai로 바꾸는 순간
    배선을 새로 하지 않아도 된다 — 그때 새로 만드는 배선이 곧 미검증 경로다.

    원장이 없으면(메모리 스토어) 감싸지 않는다. 기록할 곳이 없는데 감싸면
    상한이 늘 0원으로 보여 "예산이 지켜지고 있다"는 거짓 신호가 된다.
    """
    analyzer = inner if inner is not None else build_llm_analyzer(settings)
    if ledger is None:
        return analyzer
    if settings.llm_mode is LlmMode.OPENAI:
        # 과금 모드에서만 요율을 강제한다. 로컬은 실제로 공짜라 요율이 없는
        # 것이 정확한 상태이고, 거기에 선언을 요구하면 없는 비용을 지어낸다.
        require_pricing_for(settings.openai_model, config.budget.model_pricing)
    return BudgetedAnalyzer(
        analyzer,
        ledger=ledger,
        daily_limit_usd=config.budget.daily_llm_usd,
        pricing=config.budget.model_pricing,
    )


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

    async def approved_sell_profiles(
        self, as_of: date, tickers: tuple[str, ...]
    ) -> dict[str, frozenset[str]]:
        """Return which personas' sell judgements survived the critic today."""
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


class _MacroSource(Protocol):
    async def macro(
        self, series: str, execution_id: str
    ) -> tuple[MacroObservation, ...]:
        """Fetch a public macro series, oldest first."""
        ...


class _MacroSink(Protocol):
    async def save_macro(self, value: MacroAnalysisOutput) -> None:
        """Upsert one regime observation."""
        ...


class _UniverseSink(Protocol):
    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        """Upsert one universe snapshot."""
        ...

    async def last_known_listings(
        self, tickers: tuple[str, ...]
    ) -> dict[str, KnownListing]:
        """Return the newest universe row we ever stored per ticker."""
        ...


def build_universe_job(
    *,
    source: _UniverseSource,
    domain: _UniverseSink,
    held: TickerSource,
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

    **왜 이 잡이 보유를 알아야 하는가.** 거래 가능 범위의 정의가 "상장 피드"가
    아니라 **"상장 피드 더하기 우리가 든 것"**이기 때문이다. 그 차이는 상장폐지에서
    드러난다: 폐지된 종목이 여기서 빠지면 ``tb_daily_pick``(FK) → sell 시그널
    → close 주문 사슬이 통째로 끊겨 **팔아야 할 바로 그 종목**을 팔 수 없다.
    아래 세 갈래(픽·시그널·주문)를 고치는 대신 뿌리인 정의를 고친다.
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
        members = [
            UniverseMember(
                as_of_date=as_of,
                ticker=item.ticker,
                company_name=item.name,
                market_cap=int(item.market_cap),
                evidence_ids=(f"universe:{as_of.isoformat()}:{item.ticker}",),
            )
            for item in ranked
        ]
        # 이월은 **절단 뒤에** 더한다. 먼저 섞으면 시총이 낮은 폐지 종목이
        # universe_size에 걸려 잘려나가고 문제가 그대로 되돌아온다 — 스크리닝의
        # "보유는 캡과 무관"과 같은 원리다.
        listed = frozenset(member.ticker for member in members)
        orphans = tuple(t for t in await held(as_of) if t not in listed)
        # 한 번도 유니버스에 없던 종목은 살 수도 없었다. 안 나오면 지어내지
        # 않고 그냥 빠진다 — 없는 계보를 만드느니 스크리닝의 unlisted 보고에
        # 남는 편이 낫다.
        carried = await domain.last_known_listings(orphans)
        members.extend(
            UniverseMember(
                as_of_date=as_of,
                ticker=ticker,
                company_name=carried[ticker].company_name,
                market_cap=carried[ticker].market_cap,
                listing_status="held_delisted",
                evidence_ids=(f"universe:{as_of.isoformat()}:{ticker}:held",),
            )
            for ticker in orphans
            if ticker in carried
        )
        await domain.save_universe(
            UniverseScreenerOutput(
                run_id=f"universe:{as_of.isoformat()}",
                generated_at=datetime.combine(as_of, time(), tzinfo=UTC),
                members=tuple(members),
            )
        )
        detail = f"{len(members)} members as of {as_of.isoformat()}"
        delisted = len(members) - len(ranked)
        if delisted:
            detail += f" ({delisted} held, delisted)"
        return detail

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


class _NewsSource(Protocol):
    async def articles(self, session: date, until: date) -> tuple[RawNewsWrite, ...]:
        """Fetch the whole market's headlines for one session's window."""
        ...


class _NewsSink(Protocol):
    async def save_raw_news(self, articles: tuple[RawNewsWrite, ...]) -> None:
        """Upsert the collected headlines into the raw ledger."""
        ...


def build_news_job(
    *,
    source: _NewsSource,
    domain: _NewsSink,
    calendar: NyseCalendar,
    name: str = "news",
) -> JobDefinition:
    """Collect the whole market's headlines since the last closed session.

    공시 잡과 같은 자리에 같은 이유로 선다: 종목별 폴링이면 콜 수가 종목 수에
    비례해서 **분석 범위 밖 종목을 영영 못 본다**. 여기서는 심볼을 지정하지
    않으므로 그날 무엇을 볼지 미리 정하지 않아도 된다.

    창의 끝이 세션이 아니라 **실행일**인 이유는 어댑터 docstring에 있다 —
    주말·프리마켓 기사를 다음 실행이 다시 줍지 않기 때문이다.

    수집한 헤드라인은 투표가 아니라 분석 잡의 **증거 종합 맥락**으로 들어간다
    (출처 등급 gray). 여기서 하드 이벤트를 판정하지 않는 것도 의도다 —
    상장폐지·거래정지는 권위 있는 쪽인 SEC 폼이 판정한다. 헤드라인 키워드로
    매도를 발동시키면 "Trading Halt" 같은 제목 하나가 포지션을 날린다.
    """

    async def run(as_of: date) -> str:
        session = calendar.previous_trading_day(as_of)
        articles = await source.articles(session, as_of)
        await domain.save_raw_news(articles)
        tickers = len({article.ticker for article in articles})
        return (
            f"{len(articles)} headlines on {tickers} tickers"
            f" since {session.isoformat()}"
        )

    return JobDefinition(name=name, run=run)


def build_macro_job(
    *,
    source: _MacroSource,
    domain: _MacroSink,
    name: str = "macro",
) -> JobDefinition:
    """Collect the market regime the analysis jobs judge under.

    구 러너 role_04가 쓰던 ``tb_macro`` 행을 잡이 이어 쓴다. 이 잡이 없으면
    구 러너를 지우는 순간 ``latest_macro``가 읽을 행이 끊기고,
    ``risk_off_action``·``macro_penalty_table``이 다시 유령이 된다 — 매수
    판단이 국면을 모른 채 돌게 된다.

    산식(rate/12)과 국면 문턱은 role_04 contracts와 한 곳을 공유한다.
    ``as_of``를 자정으로 고정하는 이유는 멱등 때문이다 — 같은 날 재실행이
    같은 행을 덮어쓰고(upsert 축이 as_of), 시계에 의존하지 않는다.
    """

    async def run(as_of: date) -> str:
        run_id = f"macro:{as_of.isoformat()}"
        observations = await source.macro("DFF", run_id)
        if not observations:
            # 지어내지 않는다 — 없는 국면을 중립으로 저장하면 그 행이 이틀 동안
            # "신선한 관측"으로 판단에 들어간다. 없으면 latest_macro가 None을
            # 돌려주고 부르는 쪽은 감점도 차단도 하지 않는다.
            return "no observations"
        rate = float(observations[-1].value)
        regime, risk_score = regime_from_rate(rate)
        await domain.save_macro(
            MacroAnalysisOutput(
                run_id=run_id,
                as_of=datetime.combine(as_of, time(), tzinfo=UTC),
                regime=regime,
                risk_score=risk_score,
                vix=MVP_BASELINE_VIX,
                nasdaq_ret=MVP_BASELINE_NASDAQ_RET,
                sp500_ret=MVP_BASELINE_SP500_RET,
                rate=rate,
                dollar=MVP_BASELINE_DOLLAR,
                evidence_ids=(f"{run_id}:DFF",),
            )
        )
        return f"DFF {rate:.2f}% → {regime.value} ({risk_score:.2f})"

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


def build_insider_scoring_job(
    *,
    store: object,
    source: object,
    policy: InsiderPolicy,
    calendar: NyseCalendar,
    name: str = "insider_scoring",
) -> JobDefinition:
    """Turn today's Form 4 filings into the vote role_07 counts.

    성향 인자가 없는 것이 요점이다 — 채점은 "무엇이 사실인가"를 묻고 답이 성향과
    무관하다. 그래서 페르소나 수만큼 반복하지 않고 한 번만 돌며, 두 분석 잡이
    같은 표를 읽는다.
    """
    job = InsiderScoringJob(store=store, source=source, policy=policy)  # pyright: ignore[reportArgumentType]

    async def run(as_of: date) -> str:
        session = calendar.previous_trading_day(as_of)
        result = await job.run(as_of=as_of, session=session)
        detail = f"{len(result.scores)} insider votes"
        if result.abstained:
            # 기권 수를 적지 않으면 "2건 채점"이 "대상이 2건이었다"로 읽힌다.
            detail = f"{detail}, {result.abstained} abstained (no discretionary trade)"
        return detail

    return JobDefinition(name=name, run=run)


def build_analysis_job(  # noqa: PLR0913 - 각 인자가 교체 가능한 협력자 하나다
    *,
    store: object,
    analyzer: LlmAnalyzer,
    config: Mvp2Config,
    profile_name: str,
    calendar: NyseCalendar,
    name: str = "analysis",
) -> JobDefinition:
    """Analyse today's scope under one persona.

    성향 이름을 인자로 받는 이유: 원장의 유일성 축이 ``inv_type``이라 페르소나
    하나가 잡 하나다. 2종 팬아웃은 잡 두 개를 등록하는 일이 된다.
    """
    job = AnalysisJob(
        store=store,
        analyzer=analyzer,
        gates=config.gates,
        profile=config.profiles[profile_name],
        profile_name=profile_name,
        calendar=calendar,
        headlines_per_ticker=config.news.headlines_per_ticker,
    )

    async def run(as_of: date) -> str:
        session = calendar.previous_trading_day(as_of)
        result = await job.run(as_of=as_of, session=session)
        sides = Counter(outcome.side for outcome in result.outcomes)
        approved = sum(1 for outcome in result.outcomes if outcome.approved)
        detail = (
            f"{len(result.outcomes)} analysed ({profile_name}):"
            f" buy {sides['buy']} / sell {sides['sell']} / hold {sides['hold']},"
            f" {approved} approved"
        )
        if result.skipped:
            # 조용히 빠뜨리지 않는다 — 범위가 몇이었는지가 원장에 남아야 한다.
            detail += f", {result.skipped} skipped after model errors"
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
    """Apply the exit rules to the holdings, using the stored bars as evidence.

    관측을 두 날짜에서 조립한다: 시세·하드 이벤트는 **직전 세션**의 사실이고,
    매도 판단은 **오늘** 분석 잡이 낸 것이다. 잡 등록 순서가 분석 → 청산인
    이유가 여기 있다 — 오늘 판단이 오늘 청산에 닿으려면 그 전에 나와 있어야 한다.

    판단이 있어도 관측이 없는 종목은 더하지 않는다. 없는 관측을 지어내면
    "시세를 못 받은 날은 아무것도 하지 않는다"는 규칙이 무력해진다.
    """

    async def run(as_of: date) -> str:
        held = await tickers(as_of)
        if not held:
            return "no holdings"
        session = calendar.previous_trading_day(as_of)
        observations = await domain.exit_observations(session, held)
        judged = await domain.approved_sell_profiles(as_of, held)
        observations = {
            ticker: replace(observation, sell_signal_profiles=judged.get(ticker, frozenset()))
            for ticker, observation in observations.items()
        }
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
    news: _NewsSource | None = None
    # 실제로는 market_data와 같은 어댑터가 둘 다 구현하지만 필드를 가른다 —
    # 유니버스만 주는 테스트 조립이 macro 없는 잡을 받아 런타임에 죽으면 안 된다.
    macro: _MacroSource | None = None
    analyzer: LlmAnalyzer | None = None
    # 와이어 RSS(R11). None이면 기본 피드 2종(GNW·PRN)으로 항상 등록된다 —
    # 자격증명이 없는 소스라 등록을 조건에 걸 이유가 없다(SEC와 같은 원리).
    wire_news: _NewsSource | None = None
    # Form 4 수신(R8). None이면 실 EDGAR로 선다 — 무키라 조건이 없다.
    ownership: object | None = None


def _collection_jobs(  # noqa: PLR0913 - 협력자 목록이지 옵션 스프롤이 아니다
    selected: JobSources,
    settings: Settings,
    config: Mvp2Config,
    *,
    domain: object,
    held_tickers: TickerSource,
    covered_tickers: TickerSource,
    calendar: NyseCalendar,
) -> list[JobDefinition]:
    """Assemble the data-collection jobs, in their contractual order.

    판단 잡(스크리닝·분석·청산)과 조립을 가르는 이유는 단순히 길이가 아니다 —
    수집 잡들만 자격증명 유무에 따라 등록이 갈리고, 판단 잡은 언제나 선다.
    """
    jobs: list[JobDefinition] = []
    key = settings.alpaca_api_key.get_secret_value().strip()
    secret = settings.alpaca_secret_key.get_secret_value().strip()
    if selected.market_data is not None:
        jobs.append(
            build_universe_job(
                source=selected.market_data,
                domain=domain,
                held=held_tickers,
                config=config.screening,
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
    news_source = selected.news
    if news_source is None and key and secret:
        news_source = AlpacaNewsSource(
            key_id=key, secret_key=secret, page_size=config.news.page_size
        )
    if news_source is not None:
        # 뉴스는 공시 **뒤**, 스크리닝 **앞**이다. 판단(분석 잡)이 오늘의 증거를
        # 보려면 그 전에 수집이 끝나 있어야 하고, 하드 이벤트를 판정하는 것은
        # 공시 쪽이라 순서상 뉴스가 그것을 가로챌 자리에 있으면 안 된다.
        jobs.append(
            build_news_job(source=news_source, domain=domain, calendar=calendar)
        )
    # 와이어 보도자료(R11)는 **별도 잡**이다 — Alpaca에 합성하지 않는다. 잡
    # 격리가 이 시스템의 실패 경계라서다: 실측으로 Alpaca 키가 죽은 날에도
    # allow 등급 헤드라인 수집은 계속되어야 한다. SEC처럼 무키라 항상 선다.
    jobs.append(
        build_news_job(
            source=selected.wire_news or WireRssSource(feeds=default_wire_feeds()),
            domain=domain,
            calendar=calendar,
            name="news_wire",
        )
    )
    if selected.macro is not None:
        # 매크로는 스크리닝·분석 **앞**이다 — 분석 잡이 latest_macro로 오늘의
        # 국면을 읽으므로, 그 전에 행이 놓여 있어야 같은 틱 안에서 닿는다.
        jobs.append(build_macro_job(source=selected.macro, domain=domain))
    return jobs


def build_job_runner(
    settings: Settings,
    config: Mvp2Config,
    *,
    store: object,
    sources: JobSources | None = None,
) -> JobRunner | None:
    """Assemble the background job runner for this application, if it can run.

    잡 등록 **순서가 계약이다**: 유니버스 → 일봉 → 공시 → 뉴스 → 매크로 →
    스크리닝 → 분석 x 성향수 → 청산 → 배분. 수집이 판단보다 먼저 와야 오늘
    시세·공시로 판단하고, 유니버스가 일봉보다 먼저 와야 그 주에 새로 든 종목이
    봉 없이 남지 않는다. 청산이 배분보다 앞인 이유는 지갑이다 — 판 돈과 빈
    자리(보유 수 한도)로 사야 "자리가 없어 못 산다"가 하루 늦지 않는다. 한 틱
    안에서 순서대로 돌기 때문에 이 순서가 그대로 데이터 의존성을 만족시킨다.
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

    jobs: list[JobDefinition] = _collection_jobs(
        selected,
        settings,
        config,
        domain=domain,
        held_tickers=held_tickers,
        covered_tickers=covered_tickers,
        calendar=calendar,
    )
    jobs.append(
        build_screening_job(
            domain=domain,
            held=held_tickers,
            config=config.screening,
            calendar=calendar,
        )
    )
    # 인사이더 채점은 스크리닝 **뒤**(픽이 있어야 FK가 선다)이면서 분석 **앞**이다
    # — 한 슬롯 늦게 도착하는 증거는 증거가 아니다. SEC는 무키라 자격증명 조건이
    # 없고, 채점이 결정론이라 분석기도 필요 없다.
    jobs.append(
        build_insider_scoring_job(
            store=store,
            source=selected.ownership or SecOwnershipSource(),
            policy=config.insider,
            calendar=calendar,
        )
    )
    if selected.analyzer is not None:
        # 성향마다 한 잡. 선언 순서가 곧 실행 순서이고, 두 페르소나는 서로의
        # 결과를 보지 않는다 — 원장에서 inv_type으로 갈린다.
        jobs.extend(
            build_analysis_job(
                store=store,
                analyzer=selected.analyzer,
                config=config,
                profile_name=profile_name,
                calendar=calendar,
                name=f"analysis:{profile_name}",
            )
            for profile_name in sorted(config.profiles)
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
    # 배분은 **청산 뒤**다. 청산이 자리(보유 수 한도)와 현금을 비운 다음에
    # 사야 "자리가 없어 못 산다"가 하루 늦지 않는다. 분석 뒤인 것은 당연하고
    # — 후보 자체가 오늘 분석의 승인 결과다.
    jobs.append(
        JobDefinition(
            name="allocation",
            run=_allocation_runner(
                AllocationJob(
                    store=store,
                    broker=MockBroker(),
                    profiles=config.profiles,
                    gates=config.gates,
                    allocation=config.allocation,
                    calendar=calendar,
                )
            ),
        )
    )
    notifier = build_failure_notifier(settings)
    # 알림을 안 쓰는 설치에는 이 잡을 **등록하지 않는다.** 보낼 곳 없는 잡이
    # 매일 원장에 행 하나를 남기는 것은 유령이다.
    if notifier is not None:
        jobs.append(build_daily_summary_job(domain=domain, notify=notifier))
    return JobRunner(
        config=config.jobs,
        ledger=domain,
        jobs=tuple(jobs),
        calendar=calendar,
        # 키가 없으면 None이고, 그때 러너는 알림을 아예 시도하지 않는다.
        notifier=notifier,
    )


def build_daily_summary_job(
    *,
    domain: object,
    notify: Callable[[str], Awaitable[None]],
    name: str = "daily_summary",
) -> JobDefinition:
    """Say once a day that the chain ran, so silence becomes the signal.

    실패 알림은 앱이 살아 있을 때만 온다. 앱이 아예 안 떴거나 맥이 꺼져
    있었으면 아무 소리도 안 나고, 그 침묵은 "문제 없음"과 구별되지 않는다.
    매일 한 통을 보내면 **안 오는 것 자체가 신호**가 된다.

    잡으로 등록한 이유는 기능이 아니라 멱등이다. 하루 한 번 보장을
    ``tb_job_run``의 PK가 이미 DB로 강제하므로, 앱을 껐다 켜도 그날 이미
    보냈으면 다시 안 보낸다 — 앱 메모리에 기억하면 재시작마다 또 온다.

    **등록은 맨 끝이다.** 순서가 곧 실행 순서라, 앞의 잡들이 끝난 뒤라야
    요약할 것이 있다. 자기 자신은 세지 않는다 — 지금 도는 중이라 결과가 없다.
    """

    async def run(as_of: date) -> str:
        runs = [item for item in await domain.job_runs(as_of) if item.job_name != name]  # type: ignore[attr-defined]
        succeeded = sum(1 for item in runs if item.status == "succeeded")
        broken = [item.job_name for item in runs if item.status == "failed"]
        plans = await domain.order_plans(as_of)  # type: ignore[attr-defined]
        bought = sum(1 for plan in plans if plan.decision == "planned")
        headline = "✅" if not broken and succeeded == len(runs) else "⚠️"
        lines = [
            f"{headline} {as_of} 슬롯 · 잡 {succeeded}/{len(runs)} 성공 · 신규 매수 {bought}건"
        ]
        if broken:
            lines.append(f"실패: {', '.join(broken)}")
        await notify("\n".join(lines))
        return f"summary sent: {succeeded}/{len(runs)} succeeded, {bought} bought"

    return JobDefinition(name=name, run=run)


def _allocation_runner(job: AllocationJob) -> Callable[[date], Awaitable[str]]:
    """Adapt the allocation job's keyword-only entry point to the runner shape."""

    async def run(as_of: date) -> str:
        return await job.run(as_of=as_of)

    return run
