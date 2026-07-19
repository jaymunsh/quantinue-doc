"""PostgreSQL repository for canonical trading and delayed-review rows."""

from datetime import date
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, MetaData, Table, and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.broker.bracket_trigger import DailyRange
from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.db.contracts import AppOrderExposureStatus
from quantinue.db.domain_records import (
    AccountRiskState,
    AccountWrite,
    CloseOrderReservation,
    CompletedFillWrite,
    CriticVerdictWrite,
    DailyBarWrite,
    DailyPickWrite,
    KnownListing,
    OrderPlanWrite,
    OrderReconciliation,
    RawDisclosureWrite,
    RawNewsWrite,
    StrategistSignalWrite,
)
from quantinue.db.domain_sources import save_source_records
from quantinue.db.postgres_accounting import initialize_account, record_completed_fill
from quantinue.roles.analysis import AnalysisSubject
from quantinue.roles.exits import DailyObservation, OpenPosition
from quantinue.roles.role_01_universe_screener.contracts import UniverseScreenerOutput
from quantinue.roles.role_02_technical_analysis.contracts import TechnicalAnalysisOutput
from quantinue.roles.role_03_daily_screener.contracts import DailyScreenerOutput
from quantinue.roles.role_04_macro_analysis.contracts import MacroAnalysisOutput
from quantinue.roles.screening import RankedCandidate

_TABLES = (
    "tb_universe",
    "tb_daily_pick",
    "tb_technical",
    "tb_macro",
    "tb_disclosure",
    "tb_news",
    "tb_disclosure_signal",
    "tb_news_signal",
    "tb_strategist_signals",
    "tb_critic_verdict",
    "tb_order_plan",
    "tb_daily_bar",
    "tb_job_run",
    "tb_disclosure_raw",
    "tb_news_raw",
    "tb_account",
    "tb_order",
    "tb_fill",
)


class _IdentifierRow(BaseModel):
    model_config = ConfigDict(strict=True)
    value: int


def _is_open_position(orders: Table) -> ColumnElement[bool]:
    """Match filled buys that no close order has claimed yet.

    보유는 "체결된 매수"가 아니라 "체결됐고 아직 닫히지 않은 매수"다. 두 가지를
    걸러야 한다:

    1. ``order_type='bracket'`` — 청산 행 자체가 같은 종목으로 한 건 더 잡히면
       한 포지션이 둘로 세어진다(DISTINCT ticker라도 매수·청산이 같은 티커라
       중복은 안 나지만, 전량 청산 후에도 청산 행 때문에 보유가 남는다).
    2. 자신을 가리키는 close 행이 없을 것 — ``closes_order_id``가 실현손익의
       짝이자 "이 매수는 닫혔다"는 유일한 증거다.

    청산 주문이 아직 체결 전(submitted)이어도 열린 것으로 보지 않는다. 곧 닫힐
    포지션에 신규 매수 한도를 내주면 한도를 두 번 쓰게 된다 — 보수적으로 막는다.
    """
    closes = orders.alias("closing_order")
    return and_(
        orders.c.status == "filled",
        orders.c.order_type == "bracket",
        ~select(closes.c.id)
        .where(
            closes.c.closes_order_id == orders.c.id,
            closes.c.status.in_(("filled", "submitted")),
        )
        .exists(),
    )


# 봉 업서트 시 갱신하는 열. PK(trade_date, ticker)는 제외한다.
_BAR_VALUE_COLUMNS: Final = ("open", "high", "low", "close", "volume", "source")
# 한 문장에 싣는 봉 행 수. 백필은 수십만 행이라 통째로 보내면 드라이버가
# 파라미터를 전부 메모리에 들고 있게 된다.
_BAR_WRITE_CHUNK: Final = 5_000


# 전 유니버스 랭킹. 창 지표를 파이썬으로 계산하려면 2000종목 x 275세션(50만
# 행)을 통째로 끌어와야 한다 — 그래서 계산을 데이터가 있는 쪽에 남긴다.
# SQLAlchemy Core로 쓰지 않고 원문 SQL인 이유: 세 겹 윈도 함수는 코어 표현으로
# 옮기면 무엇을 계산하는지가 보이지 않게 된다. 실측 3.8초/53만 봉.
#
# RSI는 Wilder의 지수평활이 아니라 **단순평균(Cutler)** 이다. 윈도 프레임으로
# 표현되어 SQL 한 문장에 들어가고, 우리 용도(상위 N 줄세우기)에서 두 방식의
# 순위 차이는 미미하다 — 정밀도보다 "API 0콜"이 이 잡의 존재 이유다.
_RANK_UNIVERSE_SQL = text("""
WITH scoped AS (
    SELECT b.trade_date, b.ticker, b.close, b.volume,
           b.close - LAG(b.close) OVER w AS delta,
           LAG(b.close, 20) OVER w AS close_20,
           AVG(b.close) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
           AVG(b.close) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
           MAX(b.high) OVER (w ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_252,
           AVG(b.close * b.volume) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
               AS dollar_volume,
           AVG(b.volume) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS average_volume,
           COUNT(*) OVER w AS sessions
    FROM tb_daily_bar b
    JOIN tb_universe u ON u.ticker = b.ticker AND u.as_of_date = :universe_as_of
    WHERE b.trade_date <= :session
    WINDOW w AS (PARTITION BY b.ticker ORDER BY b.trade_date)
),
scored AS (
    SELECT trade_date, ticker, close, volume, close_20, ma20, ma50, high_252,
           dollar_volume, average_volume, sessions,
           AVG(GREATEST(delta, 0)) OVER r AS avg_gain,
           AVG(GREATEST(-delta, 0)) OVER r AS avg_loss
    FROM scoped
    WINDOW r AS (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
)
SELECT ticker, close, ma20, ma50, high_252, volume, average_volume,
       100 * (close - close_20) / close_20 AS ret_20d_pct,
       CASE WHEN avg_loss = 0 THEN 100
            ELSE 100 - 100 / (1 + avg_gain / avg_loss) END AS rsi
FROM scored
WHERE trade_date = :session
  AND close_20 IS NOT NULL
  AND close >= :min_price
  AND dollar_volume >= :min_dollar_volume
  AND sessions >= :min_sessions
""")


# 그날의 분석 범위에서 빠진 행을 지우되, 이미 무언가가 매달린 행은 남긴다.
# 참조 테이블 여섯은 tb_daily_pick을 FK로 거는 전부다(카탈로그 대조 확인).
# 통째로 DELETE 하면 구 러너와 같은 날짜를 쓰는 평일마다 FK 위반으로 잡이 죽는다.
_PRUNE_UNREFERENCED_PICKS_SQL = text("""
DELETE FROM tb_daily_pick p
WHERE p.trade_date = ANY(:days)
  AND NOT EXISTS (SELECT 1 FROM tb_technical t
                  WHERE t.trade_date = p.trade_date AND t.ticker = p.ticker)
  AND NOT EXISTS (SELECT 1 FROM tb_disclosure d
                  WHERE d.trade_date = p.trade_date AND d.ticker = p.ticker)
  AND NOT EXISTS (SELECT 1 FROM tb_disclosure_signal ds
                  WHERE ds.trade_date = p.trade_date AND ds.ticker = p.ticker)
  AND NOT EXISTS (SELECT 1 FROM tb_news n
                  WHERE n.trade_date = p.trade_date AND n.ticker = p.ticker)
  AND NOT EXISTS (SELECT 1 FROM tb_news_signal ns
                  WHERE ns.trade_date = p.trade_date AND ns.ticker = p.ticker)
  AND NOT EXISTS (SELECT 1 FROM tb_strategist_signals s
                  WHERE s.trade_date = p.trade_date AND s.ticker = p.ticker)
""")


# 오늘의 분석 대상 한 줄에 필요한 전부: 스크리닝이 정한 순위·점수, 직전 세션의
# 봉, 그리고 그 전 세션의 종가(크리틱의 급등락 게이트가 요구한다).
# 종목마다 따로 묻지 않는 이유는 이 잡의 존재 이유와 같다 — 20종목이면 20왕복이다.
_ANALYSIS_SUBJECTS_SQL = text("""
SELECT p.ticker, p.rank, p.score, p.bucket,
       b.close, b.high, b.low,
       LAG(b.close) OVER (PARTITION BY p.ticker ORDER BY b.trade_date) AS close_prev
FROM tb_daily_pick p
JOIN tb_daily_bar b ON b.ticker = p.ticker AND b.trade_date <= :session
WHERE p.trade_date = :as_of
  AND b.trade_date > :session - INTERVAL '10 days'
ORDER BY p.rank, b.trade_date
""")


class PostgresDomainRepository:
    """Idempotent canonical-domain adapter with real database identifiers."""

    def __init__(self, database_url: str) -> None:
        """Create a lazy async engine for the domain schema."""
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._metadata = MetaData()

    async def initialize(self) -> None:
        """Reflect the canonical schema after bootstrap."""
        async with self._engine.begin() as connection:
            await connection.run_sync(self._metadata.reflect, only=_TABLES)

    async def close(self) -> None:
        """Dispose all pooled database connections."""
        await self._engine.dispose()

    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        """Upsert the validated role-01 members without synthesizing parents."""
        table = self._table("tb_universe")
        async with self._engine.begin() as connection:
            for member in value.members:
                fields = member.model_dump(exclude={"evidence_ids"})
                statement = (
                    insert(table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["as_of_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)

    async def last_known_listings(
        self, tickers: tuple[str, ...]
    ) -> dict[str, KnownListing]:
        """Return each ticker's most recent universe row, if it ever had one.

        상장폐지된 보유를 이월할 때 회사명·시총의 출처다. 시총이 오래된 값인
        것은 알면서 쓰는 것이다 — 대안은 0이고, 0은 정렬에서 맨 뒤로 보내
        나중에 절단 로직이 바뀌는 순간 문제를 조용히 되돌린다. 마지막으로
        시장이 매긴 값이 "모른다"보다 정직하다.
        """
        if not tickers:
            return {}
        table = self._table("tb_universe")
        # DISTINCT ON: 티커마다 가장 최신 스냅샷 1행. 상관 서브쿼리보다
        # 한 번의 정렬로 끝나고, 이월 대상은 많아야 보유 수만큼이다.
        statement = (
            select(table.c.ticker, table.c.company_name, table.c.market_cap)
            .where(table.c.ticker.in_(tickers))
            .order_by(table.c.ticker, table.c.as_of_date.desc())
            .distinct(table.c.ticker)
        )
        async with self._engine.begin() as connection:
            rows = (await connection.execute(statement)).all()
        return {
            row.ticker: KnownListing(
                company_name=row.company_name, market_cap=int(row.market_cap)
            )
            for row in rows
        }

    async def universe_tickers(self, as_of: date) -> tuple[str, ...]:
        """Return one snapshot's tickers, largest first.

        시총 내림차순을 저장 왕복 뒤에도 유지한다 — 상위 N을 자르는 소비자가
        여럿이고(스크리닝·일봉 수집), 순서가 흔들리면 "상위"가 의미를 잃는다.
        """
        table = self._table("tb_universe")
        statement = (
            select(table.c.ticker)
            .where(table.c.as_of_date == as_of)
            .order_by(table.c.market_cap.desc(), table.c.ticker)
        )
        async with self._engine.begin() as connection:
            rows = (await connection.execute(statement)).all()
        return tuple(row.ticker for row in rows)

    async def save_daily_stage(
        self, picks: DailyScreenerOutput, technical: TechnicalAnalysisOutput
    ) -> None:
        """Persist role-03 parents and selected role-02 children atomically."""
        pick_table = self._table("tb_daily_pick")
        technical_table = self._table("tb_technical")
        async with self._engine.begin() as connection:
            for pick in picks.picks:
                fields = pick.model_dump(exclude={"evidence_ids", "is_requested_focus"})
                statement = (
                    insert(pick_table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["trade_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)
            selected = frozenset((pick.trade_date, pick.ticker) for pick in picks.picks)
            for snapshot in technical.snapshots:
                if (snapshot.trade_date, snapshot.ticker) not in selected:
                    continue
                fields = snapshot.model_dump(exclude={"evidence_ids", "is_requested_focus"})
                statement = (
                    insert(technical_table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["trade_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)

    async def save_macro(self, value: MacroAnalysisOutput) -> None:
        """Upsert the validated role-04 macro observation."""
        table = self._table("tb_macro")
        fields = value.model_dump(exclude={"run_id", "evidence_ids"})
        statement = (
            insert(table)
            .values(**fields)
            .on_conflict_do_update(index_elements=["as_of"], set_=fields)
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)

    async def save_daily_bars(self, bars: tuple[DailyBarWrite, ...]) -> None:
        """Upsert one session's bars, letting a later collection correct an earlier one.

        증분 적재는 같은 날을 두 번 받을 수 있다(재시도·정정). PK가 하루 1행을
        고정하고, 충돌 시 **최신 값이 이긴다** — 거래소 정정이 반영되어야 하고,
        먼저 들어온 값을 지키면 틀린 값이 영구히 남는다.
        """
        if not bars:
            return
        table = self._table("tb_daily_bar")
        statement = insert(table)
        statement = statement.on_conflict_do_update(
            index_elements=["trade_date", "ticker"],
            set_={name: statement.excluded[name] for name in _BAR_VALUE_COLUMNS},
        )
        rows = [
            {
                "trade_date": bar.trade_date,
                "ticker": bar.ticker,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "source": bar.source,
            }
            for bar in bars
        ]
        async with self._engine.begin() as connection:
            # 행마다 한 번씩 왕복하면 이력 백필(2000종목 x 260세션, 약 50만 행)이
            # 사실상 끝나지 않는다. 한 문장에 여러 행을 실어 보내되, 한 번에
            # 전부 싣지는 않는다 — 드라이버가 파라미터를 통째로 들고 있어야 해서
            # 메모리가 행 수에 비례해 튄다.
            for start in range(0, len(rows), _BAR_WRITE_CHUNK):
                _ = await connection.execute(
                    statement, rows[start : start + _BAR_WRITE_CHUNK]
                )

    async def rank_universe(
        self,
        session: date,
        universe_as_of: date,
        *,
        min_price_usd: float,
        min_avg_dollar_vol: float,
        min_history_sessions: int,
    ) -> tuple[RankedCandidate, ...]:
        """Rank the whole universe from stored bars, spending zero API calls.

        구 경로(role_02)는 지표를 종목당 1콜로 받아 500종목 캡이 필요했다.
        봉이 원장에 있으면 그 캡의 근거가 사라진다 — 계산은 공짜고, 유동성
        필터도 같은 문장 안에서 끝난다.

        유동성·최소 이력 미달로 걸러진 종목은 **그냥 빠진다**. 점수 0으로
        넣으면 하위권에 섞여 "봤지만 나빴다"로 읽히는데, 사실은 "볼 수 없었다"다.
        보유 종목이 여기서 빠지는 경우는 스코프 규칙이 따로 건진다.
        """
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    _RANK_UNIVERSE_SQL,
                    {
                        "session": session,
                        "universe_as_of": universe_as_of,
                        "min_price": min_price_usd,
                        "min_dollar_volume": min_avg_dollar_vol,
                        "min_sessions": min_history_sessions,
                    },
                )
            ).all()
        return tuple(
            RankedCandidate(
                ticker=row.ticker,
                close=Decimal(str(row.close)),
                ret_20d_pct=float(row.ret_20d_pct),
                ma20=Decimal(str(row.ma20)),
                ma50=Decimal(str(row.ma50)),
                high_252=Decimal(str(row.high_252)),
                rsi=float(row.rsi),
                volume=int(row.volume),
                average_volume=float(row.average_volume),
            )
            for row in rows
        )

    async def save_daily_picks(self, picks: tuple[DailyPickWrite, ...]) -> None:
        """Replace the session's analysis scope with this one, atomically.

        먼저 **아무도 참조하지 않는** 그날 행을 지우고, 그 위에 업서트한다.
        지우는 이유: 순위는 집합 전체에 대한 상대값이라 범위에서 빠진 어제의
        20위가 남아 있으면 "오늘의 상위 N"이 거짓이 된다.

        통째로 지우지 않는 이유: 구 11단계 러너가 사라질 때까지(D6 점진 교체)
        같은 날짜에 두 경로가 함께 쓴다. 이미 판단(시그널·지표·공시·뉴스)이
        매달린 행까지 지우려 들면 FK에 걸려 스크리닝 잡이 그날 통째로 실패한다.
        참조된 행은 남기고 새 순위로 갱신하는 편이 정직하다 — 그 행은 실제로
        오늘 누군가 본 종목이기 때문이다.

        같은 날 두 번 돌면 두 번째가 이긴다 — 재실행이 곧 정정이다.
        """
        if not picks:
            return
        table = self._table("tb_daily_pick")
        sessions = sorted({pick.trade_date for pick in picks})
        fields = ("universe_as_of", "bucket", "rank", "sector", "score")
        statement = insert(table)
        statement = statement.on_conflict_do_update(
            index_elements=["trade_date", "ticker"],
            set_={name: statement.excluded[name] for name in fields},
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(_PRUNE_UNREFERENCED_PICKS_SQL, {"days": sessions})
            _ = await connection.execute(
                statement,
                [
                    {
                        "trade_date": pick.trade_date,
                        "ticker": pick.ticker,
                        "universe_as_of": pick.universe_as_of,
                        "bucket": pick.bucket,
                        "rank": pick.rank,
                        "sector": pick.sector,
                        "score": pick.score,
                    }
                    for pick in picks
                ],
            )

    async def analysis_subjects(self, as_of: date, session: date) -> tuple[AnalysisSubject, ...]:
        """Load today's scope together with the prices the gates need.

        봉을 한 세션만 읽지 않는 이유: 크리틱의 급등락 게이트가 **전일 종가**를
        요구하는데, 거래정지나 휴장으로 직전 세션 바로 앞이 비어 있을 수 있다.
        10일 창을 읽고 각 종목의 마지막 두 봉만 취한다.

        봉이 없는 종목(거래정지·상장폐지)은 빠진다. 값을 매길 수 없는 종목에
        판단을 붙이면 그 판단이 무엇을 근거로 하는지 말할 수 없다 — 그런
        종목의 청산은 하드 이벤트를 보는 청산 잡의 몫이다.
        """
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    _ANALYSIS_SUBJECTS_SQL, {"as_of": as_of, "session": session}
                )
            ).all()
        # 종목별 마지막 행이 곧 가장 최근 봉이다(쿼리가 날짜 오름차순).
        latest: dict[str, AnalysisSubject] = {}
        for row in rows:
            latest[row.ticker] = AnalysisSubject(
                ticker=row.ticker,
                rank=int(row.rank),
                score=float(row.score),
                bucket=row.bucket,
                close=Decimal(str(row.close)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close_prev=(
                    None if row.close_prev is None else Decimal(str(row.close_prev))
                ),
            )
        return tuple(sorted(latest.values(), key=lambda item: item.rank))

    async def disclosure_evidence(
        self, session: date, tickers: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        """Return the session's filing form types per ticker, for the analysis prompt.

        원시 원장을 읽는다 — ``tb_disclosure``는 그날 픽에만 행을 넣을 수 있어서
        범위 밖 종목을 못 담는다(그래서 원시 원장이 따로 있다).
        """
        if not tickers:
            return {}
        table = self._table("tb_disclosure_raw")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(table.c.ticker, table.c.form_type)
                    .where(table.c.trade_date == session, table.c.ticker.in_(tickers))
                    .order_by(table.c.ticker, table.c.form_type)
                )
            ).all()
        found: dict[str, list[str]] = {}
        for row in rows:
            found.setdefault(row.ticker, []).append(row.form_type)
        return {ticker: tuple(forms) for ticker, forms in found.items()}

    async def news_evidence(
        self, session: date, tickers: tuple[str, ...], limit: int
    ) -> dict[str, tuple[str, ...]]:
        """Return the newest headlines per ticker, for the analysis prompt.

        ``limit``이 인자인 이유: 프롬프트에 들어가는 헤드라인 수는 정책이지
        구현 세부가 아니다(``news.headlines_per_ticker``). 여기서 자르는 이유는
        20종목에 성향 2를 매일 도는 잡이라, 안 자르면 종목 하나가 시끄러운 날
        그 종목만으로 컨텍스트가 채워진다.

        최신순으로 자른다 — 예산에 걸려 버려지는 것은 오래된 쪽이어야 한다.
        """
        if not tickers or limit <= 0:
            return {}
        table = self._table("tb_news_raw")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(table.c.ticker, table.c.headline)
                    .where(table.c.trade_date == session, table.c.ticker.in_(tickers))
                    .order_by(table.c.ticker, table.c.published_at.desc())
                )
            ).all()
        found: dict[str, list[str]] = {}
        for row in rows:
            headlines = found.setdefault(row.ticker, [])
            if len(headlines) < limit:
                headlines.append(row.headline)
        return {ticker: tuple(headlines) for ticker, headlines in found.items()}

    async def save_raw_news(self, articles: tuple[RawNewsWrite, ...]) -> None:
        """Upsert the collected headlines into the raw ledger.

        수집 창이 세션부터 실행일까지라 **매번 겹친다**. 겹침을 원장이 흡수해야
        하므로 (기사, 티커)로 충돌을 잡고 갱신한다 — 기사는 정정되기도 한다.
        """
        if not articles:
            return
        table = self._table("tb_news_raw")
        async with self._engine.begin() as connection:
            for article in articles:
                fields = {
                    "trade_date": article.trade_date,
                    "headline": article.headline,
                    "source": article.source,
                    "url": article.url,
                    "published_at": article.published_at,
                }
                _ = await connection.execute(
                    insert(table)
                    .values(
                        article_id=article.article_id,
                        ticker=article.ticker,
                        **fields,
                    )
                    .on_conflict_do_update(
                        index_elements=["article_id", "ticker"], set_=fields
                    )
                )

    async def bar_coverage(self) -> dict[str, date]:
        """Return the newest stored bar date per ticker.

        수집 잡이 "무엇을 아직 모르는가"를 묻는 유일한 통로다. 종목 목록을
        인자로 받지 않는 이유: 2000개짜리 IN 절을 만드는 것보다 원장 전체를
        한 번 훑는 편이 싸고, 어차피 잡이 자기 목록과 교집합을 낸다.
        """
        table = self._table("tb_daily_bar")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(table.c.ticker, func.max(table.c.trade_date).label("newest"))
                    .group_by(table.c.ticker)
                )
            ).all()
        return {row.ticker: row.newest for row in rows}

    async def daily_bars(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyBarWrite]:
        """Read the requested session's bars, omitting whatever was never collected.

        없는 종목을 0이나 전일 값으로 채우지 않는다. 청산 잡은 관측이 없으면
        아무것도 하지 않게 되어 있는데, 여기서 지어내면 그 안전장치가 무력해진다.
        """
        if not tickers:
            return {}
        table = self._table("tb_daily_bar")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(table).where(
                        table.c.trade_date == trade_date,
                        table.c.ticker.in_(tickers),
                    )
                )
            ).all()
        return {
            row.ticker: DailyBarWrite(
                trade_date=row.trade_date,
                ticker=row.ticker,
                open=Decimal(str(row.open)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close=Decimal(str(row.close)),
                volume=int(row.volume),
                source=row.source,
            )
            for row in rows
        }

    async def save_raw_disclosures(self, filings: tuple[RawDisclosureWrite, ...]) -> None:
        """Upsert one session's whole-market filings.

        접수번호가 PK다. 수집은 재시도될 수 있는데 (날짜, 티커)로는 행을 못
        가른다 — 같은 종목이 같은 날 여러 건을 낸다.
        """
        if not filings:
            return
        table = self._table("tb_disclosure_raw")
        async with self._engine.begin() as connection:
            for filing in filings:
                fields = {
                    "trade_date": filing.trade_date,
                    "ticker": filing.ticker,
                    "cik": filing.cik,
                    "form_type": filing.form_type,
                    "company_name": filing.company_name,
                    "source_ref": filing.source_ref,
                    "event_type": filing.event_type,
                    "is_hard_event": filing.is_hard_event,
                }
                _ = await connection.execute(
                    insert(table)
                    .values(filing_no=filing.filing_no, **fields)
                    .on_conflict_do_update(index_elements=["filing_no"], set_=fields)
                )

    async def hard_event_tickers(self, trade_date: date) -> frozenset[str]:
        """Return the tickers whose day carried a hard event."""
        table = self._table("tb_disclosure_raw")
        statement = select(table.c.ticker).where(
            table.c.trade_date == trade_date, table.c.is_hard_event.is_(True)
        )
        async with self._engine.begin() as connection:
            rows = (await connection.execute(statement)).all()
        return frozenset(row.ticker for row in rows)

    async def exit_observations(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> dict[str, DailyObservation]:
        """Project stored bars and hard events into what the exit rules consume.

        일봉이 청산의 두 입력을 준다: 고저는 브래킷 발동 판정에, 종가는 시간
        청산의 기준가에. 하드 이벤트(상장폐지·등록말소)는 공시 원장에서 온다.

        **관측 키를 봉 기준으로만 만들면 안 된다.** 거래가 정지되면 그날 봉이
        찍히지 않는데, 그게 정확히 상장폐지 케이스다 — 봉만 보면 팔아야 할 바로
        그 종목이 관측에서 조용히 사라진다. 그래서 두 집합의 합집합으로 만든다.
        """
        bars = await self.daily_bars(trade_date, tickers)
        hard_events = await self.hard_event_tickers(trade_date)
        observed = set(bars) | (hard_events & set(tickers))
        return {
            ticker: DailyObservation(
                day_range=(
                    DailyRange(low=bars[ticker].low, high=bars[ticker].high)
                    if ticker in bars
                    else None
                ),
                last_price=bars[ticker].close if ticker in bars else None,
                has_hard_event=ticker in hard_events,
            )
            for ticker in observed
        }

    async def approved_sell_profiles(
        self, as_of: date, tickers: tuple[str, ...]
    ) -> dict[str, frozenset[str]]:
        """Return which personas' sell judgements survived the critic today.

        청산 3층의 soft path다 — 하드 이벤트가 없는 논지 붕괴는 여기로만 나간다.

        **크리틱을 통과한 것만 센다.** 매도는 되돌릴 수 없으므로, 반박당한
        판단으로 파는 것은 반박을 안 한 것보다 나쁘다(패닉 매도 방어선).
        조인이 곧 필터라서, 크리틱 행이 없는 시그널은 자연히 빠진다 — 청산
        잡 자신이 남기는 기계적 sell 시그널(``run_id='exit:...'``)이 정확히
        그런 행이고, 그래서 자기가 만든 시그널을 다시 읽어 두 번 파는 일이 없다.

        성향을 접지 않고 집합으로 돌려주는 이유: 포지션도 성향별이라 어느
        성향이 팔라고 했는지가 곧 어느 계좌를 닫을지다.
        """
        if not tickers:
            return {}
        signals = self._table("tb_strategist_signals")
        verdicts = self._table("tb_critic_verdict")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(signals.c.ticker, signals.c.inv_type)
                    .join(verdicts, verdicts.c.signal_id == signals.c.id)
                    .where(
                        signals.c.trade_date == as_of,
                        signals.c.ticker.in_(tickers),
                        signals.c.side == "sell",
                        verdicts.c.decision == "pass",
                    )
                )
            ).all()
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(row.ticker, set()).add(row.inv_type)
        return {ticker: frozenset(profiles) for ticker, profiles in found.items()}

    async def reserve_job_run(self, job_name: str, slot_date: date) -> bool:
        """Claim today's slot for one job, returning whether this caller won it.

        스케줄러는 60초마다 깨어나므로 "이미 돌았나"를 앱 메모리로 판단하면
        재시작 한 번에 무너진다. PK 충돌을 예약 실패로 읽으면 판정이 DB에
        있게 되고, 프로세스가 여럿이어도 잡 본문은 한 번만 돈다.

        단 **실패한 슬롯은 같은 날 다시 집을 수 있다**. 예약 행이 남는다는
        이유로 재시도를 막으면, 수집이 한 번 실패한 날은 하루 종일 묵은 봉으로
        청산 판단을 하게 된다 — 일시적 장애가 하루짜리 눈감기로 번진다.
        ``running``과 ``succeeded``는 그대로 잠긴다: 도는 중인 걸 다시 집으면
        같은 날 두 번 돌고, 끝난 걸 다시 집으면 주기가 무의미해진다.
        """
        table = self._table("tb_job_run")
        statement = (
            insert(table)
            .values(job_name=job_name, slot_date=slot_date, status="running")
            .on_conflict_do_update(
                index_elements=["job_name", "slot_date"],
                set_={"status": "running", "detail": None, "finished_at": None},
                where=table.c.status == "failed",
            )
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
        return result.rowcount == 1

    async def finish_job_run(
        self,
        job_name: str,
        slot_date: date,
        *,
        succeeded: bool,
        detail: str | None = None,
    ) -> None:
        """Close out a reserved slot with its outcome."""
        table = self._table("tb_job_run")
        statement = (
            table.update()
            .where(table.c.job_name == job_name, table.c.slot_date == slot_date)
            .values(
                status="succeeded" if succeeded else "failed",
                detail=detail,
                finished_at=func.now(),
            )
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)

    async def last_job_success(self, job_name: str) -> date | None:
        """Return the last slot this job actually completed, if any.

        예약(running)이나 실패(failed)는 세지 않는다. 이 값이 주기 판정의
        입력이므로(``is_job_due``), 실패한 실행을 성공으로 세면 다음 주기까지
        재시도가 막힌다 — 주간 잡이면 한 주를 잃는다.
        """
        table = self._table("tb_job_run")
        statement = select(func.max(table.c.slot_date)).where(
            table.c.job_name == job_name, table.c.status == "succeeded"
        )
        async with self._engine.begin() as connection:
            return (await connection.execute(statement)).scalar_one_or_none()

    async def active_accounts(self) -> tuple[AccountRiskState, ...]:
        """Return every account that subscribes to this cycle, in stable order.

        Order matters: an unstable one would let a different account exhaust
        the daily caps on each run.
        """
        accounts = self._table("tb_account")
        orders = self._table("tb_order")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(
                        accounts.c.id,
                        accounts.c.cash,
                        accounts.c.equity,
                        accounts.c.inv_type,
                    )
                    .where(accounts.c.status == "active")
                    .order_by(accounts.c.id)
                )
            ).all()
            held = dict(
                (
                    await connection.execute(
                        select(
                            orders.c.account_id,
                            func.count(func.distinct(orders.c.ticker)),
                        )
                        .where(_is_open_position(orders))
                        .group_by(orders.c.account_id)
                    )
                ).all()
            )
        return tuple(
            AccountRiskState(
                account_id=row.id,
                cash=Decimal(str(row.cash)),
                equity=Decimal(str(row.equity)),
                open_position_count=int(held.get(row.id, 0)),
                inv_type=row.inv_type,
            )
            for row in rows
        )

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        """List every unclosed filled entry with the terms the exit rules need.

        ``account_risk_state``와 **같은 술어**(``_is_open_position``)를 쓴다.
        둘이 갈라지면 한도는 보유가 있다고 보는데 청산 잡은 없다고 보는,
        디버깅하기 최악인 상태가 된다.

        체결일은 ``tb_fill``에서 온다 — 주문 생성일이 아니다. 계획만 세우고
        체결되지 않은 주문은 보유가 아니므로 보유 기간이 시작되지 않는다.
        부분체결이 여럿이면 가장 이른 체결을 진입 시점으로 본다.
        """
        orders = self._table("tb_order")
        fills = self._table("tb_fill")
        signals = self._table("tb_strategist_signals")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(
                        orders.c.id,
                        orders.c.signal_id,
                        orders.c.account_id,
                        orders.c.ticker,
                        orders.c.quantity,
                        orders.c.entry_price,
                        orders.c.stop_price,
                        orders.c.take_profit_price,
                        # 청산 시그널이 물려받을 성향. 계좌가 아니라 진입 시그널이
                        # 출처인 이유는 OpenPosition 주석 참조.
                        signals.c.inv_type,
                        func.min(fills.c.filled_at).label("first_filled_at"),
                    )
                    .select_from(
                        orders.join(fills, fills.c.order_id == orders.c.id).join(
                            signals, signals.c.id == orders.c.signal_id
                        )
                    )
                    .where(_is_open_position(orders), fills.c.side == "buy")
                    .group_by(
                        orders.c.id,
                        orders.c.signal_id,
                        orders.c.account_id,
                        orders.c.ticker,
                        orders.c.quantity,
                        orders.c.entry_price,
                        orders.c.stop_price,
                        orders.c.take_profit_price,
                        signals.c.inv_type,
                    )
                    # 순서를 고정한다 — 청산도 일일 예산을 쓰게 되면 실행마다
                    # 다른 포지션이 먼저 처리되는 일이 없어야 한다.
                    .order_by(orders.c.id)
                )
            ).all()
        return tuple(
            OpenPosition(
                order_id=int(row.id),
                signal_id=int(row.signal_id),
                account_id=int(row.account_id),
                ticker=row.ticker,
                quantity=int(row.quantity),
                entry_price=Decimal(str(row.entry_price)),
                stop_price=(
                    None if row.stop_price is None else Decimal(str(row.stop_price))
                ),
                take_profit_price=(
                    None
                    if row.take_profit_price is None
                    else Decimal(str(row.take_profit_price))
                ),
                filled_on=row.first_filled_at.date(),
                inv_type=row.inv_type,
            )
            for row in rows
        )

    async def ensure_holding_in_scope(self, trade_date: date, ticker: str) -> bool:
        """Register a held ticker as in scope for this day, so a signal can exist.

        ``tb_strategist_signals``는 ``(trade_date, ticker) → tb_daily_pick``을
        참조한다. 즉 "그날 분석 대상이 아니었던 종목에는 판단을 남길 수 없다"는
        제약이다. 매수만 있을 때는 자연히 지켜졌지만(사려면 후보여야 하니까),
        청산은 **스크리너에서 탈락한 보유 종목**에도 일어난다.

        재설계에서 그날의 분석 대상은 "상위 N과 보유의 합집합"이다 — 보유는 정의상 범위
        안이다. 그래서 이건 제약을 우회하는 게 아니라 그 사실을 기록하는 것이고,
        ``bucket='backfill'``이 "스크리닝이 고른 게 아니다"를 정직하게 말한다.
        (Phase 3의 스크리닝 잡이 이 일을 넘겨받으면 여기서는 사라진다.)

        Returns whether the ticker could be put in scope at all.
        """
        picks = self._table("tb_daily_pick")
        universe = self._table("tb_universe")
        async with self._engine.begin() as connection:
            existing = await connection.scalar(
                select(picks.c.ticker).where(
                    picks.c.trade_date == trade_date, picks.c.ticker == ticker
                )
            )
            if existing is not None:
                return True
            # 계보의 뿌리는 유니버스다. 한 번도 유니버스에 없던 종목은 보유일 수
            # 없으므로, 없으면 지어내지 않고 실패로 보고한다.
            universe_as_of = await connection.scalar(
                select(universe.c.as_of_date)
                .where(universe.c.ticker == ticker)
                .order_by(universe.c.as_of_date.desc())
                .limit(1)
            )
            if universe_as_of is None:
                return False
            _ = await connection.execute(
                insert(picks)
                .values(
                    trade_date=trade_date,
                    ticker=ticker,
                    universe_as_of=universe_as_of,
                    bucket="backfill",
                    # 순위는 스크리닝이 매기는 값이다. 보유는 순위로 들어온 게
                    # 아니므로 최하위를 써서 상위 후보와 섞이지 않게 한다.
                    rank=50,
                    sector="held",
                    score=0,
                )
                .on_conflict_do_nothing(index_elements=["trade_date", "ticker"])
            )
            return True

    async def reserve_close_order(self, request: CloseOrderReservation) -> int | None:
        """Insert one close order, or None when this entry is already being closed.

        멱등의 축이 둘이다:

        - ``idempotency_key`` — 같은 청산의 재시도. 같은 키가 이미 있으면 그
          주문 id를 그대로 돌려줘 재시도가 이어서 진행되게 한다.
        - ``closes_order_id`` — **다른** 키로 같은 매수를 닫으려는 시도. 이게
          더 위험하다: 키가 다르면 위 검사를 통과해 같은 포지션을 두 번 팔게
          되고, 갖고 있지도 않은 주식을 판 상태가 된다. 그래서 별도로 막는다.

        주문 규모 한도(``reserve_daily_order``)를 태우지 않는 이유: 청산은 자본을
        쓰는 행동이 아니라 되돌려받는 행동이다. 한도로 청산을 막으면 리스크를
        줄이려는 시도가 한도 때문에 실패한다.
        """
        orders = self._table("tb_order")
        async with self._engine.begin() as connection:
            existing = await connection.scalar(
                select(orders.c.id).where(
                    orders.c.idempotency_key == request.idempotency_key
                )
            )
            if existing is not None:
                return int(existing)
            already_closing = await connection.scalar(
                select(orders.c.id).where(
                    orders.c.closes_order_id == request.closes_order_id,
                    orders.c.status.in_(("planned", "submitted", "filled")),
                )
            )
            if already_closing is not None:
                return None
            return await connection.scalar(
                insert(orders)
                .values(
                    signal_id=request.signal_id,
                    account_id=request.account_id,
                    ticker=request.ticker,
                    quantity=request.quantity,
                    entry_price=request.reference_price,
                    status="planned",
                    idempotency_key=request.idempotency_key,
                    order_type="close",
                    closes_order_id=request.closes_order_id,
                )
                .returning(orders.c.id)
            )

    async def revalue_accounts(self, trade_date: date) -> dict[int, Decimal]:
        """Mark every active account to the session close and persist the equity.

        재설계 D8. 그 전까지 ``tb_account.equity``는 **최초 자본에 동결**돼
        있었다 — 매수는 현금만 차감했고 보유의 시가평가가 어디에도 없었다
        (ghost 감사 §2). 그래서 ``daily_loss_limit`` 같은 미실현손익 기반
        서킷이 구조적으로 발동할 수 없었다.

        평가식은 **현금(원장) + 보유수량 * 종가**다. 종가가 없는 보유는 진입가로
        평가한다 — 0으로 두면 시세 수집이 한 번 실패한 날 계좌가 파산한 것처럼
        보이고, 그 거짓 손실이 서킷을 발동시킨다.

        멱등하다. 현금은 원장에서 다시 읽고 보유는 매번 새로 계산하므로,
        같은 날 두 번 돌려도 값이 누적되지 않는다.
        """
        accounts = self._table("tb_account")
        orders = self._table("tb_order")
        bars = self._table("tb_daily_bar")
        revalued: dict[int, Decimal] = {}
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(accounts.c.id, accounts.c.cash)
                    .where(accounts.c.status == "active")
                    .order_by(accounts.c.id)
                )
            ).all()
            holdings = (
                await connection.execute(
                    select(
                        orders.c.account_id,
                        orders.c.quantity,
                        orders.c.entry_price,
                        bars.c.close,
                    )
                    .select_from(
                        orders.outerjoin(
                            bars,
                            and_(
                                bars.c.ticker == orders.c.ticker,
                                bars.c.trade_date == trade_date,
                            ),
                        )
                    )
                    .where(_is_open_position(orders))
                )
            ).all()
            market_value: dict[int, Decimal] = {}
            for holding in holdings:
                mark = (
                    Decimal(str(holding.entry_price))
                    if holding.close is None
                    else Decimal(str(holding.close))
                )
                account_id = int(holding.account_id)
                market_value[account_id] = market_value.get(
                    account_id, Decimal(0)
                ) + Decimal(holding.quantity) * mark
            for row in rows:
                account_id = int(row.id)
                equity = Decimal(str(row.cash)) + market_value.get(account_id, Decimal(0))
                _ = await connection.execute(
                    accounts.update()
                    .where(accounts.c.id == account_id)
                    .values(equity=equity)
                )
                revalued[account_id] = equity
        return revalued

    async def account_risk_state(self, account_id: int) -> AccountRiskState | None:
        """Read the capital and book size the portfolio limits are applied to.

        Positions are derived from filled buys that nothing has closed yet — see
        ``_is_open_position``.
        """
        accounts = self._table("tb_account")
        orders = self._table("tb_order")
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    select(
                        accounts.c.cash, accounts.c.equity, accounts.c.inv_type
                    ).where(accounts.c.id == account_id)
                )
            ).first()
            if row is None:
                return None
            held = await connection.scalar(
                select(func.count(func.distinct(orders.c.ticker))).where(
                    orders.c.account_id == account_id,
                    _is_open_position(orders),
                )
            )
        return AccountRiskState(
            account_id=account_id,
            cash=Decimal(str(row.cash)),
            equity=Decimal(str(row.equity)),
            open_position_count=int(held or 0),
            inv_type=row.inv_type,
        )

    async def save_order_plan(self, value: OrderPlanWrite) -> None:
        """Record role 09's decision — including the ones that blocked a buy.

        Only orders that exist leave a tb_order row, so without this a guard
        that fired was invisible to SQL and threshold calibration had nothing
        to count.
        """
        table = self._table("tb_order_plan")
        statement = (
            insert(table)
            .values(
                run_id=value.run_id,
                ticker=value.ticker,
                cycle_ts=value.cycle_ts,
                trade_date=value.trade_date,
                account_id=value.account_id,
                signal_id=value.signal_id,
                decision=value.decision,
                skipped_reason=value.skipped_reason,
                quantity=value.quantity,
                entry_price=value.entry_price,
                stop_price=value.stop_price,
                take_profit_price=value.take_profit_price,
            )
            .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts", "account_id"])
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)

    async def save_signal(self, value: StrategistSignalWrite) -> int:
        """Insert or reuse a strategist signal and return its real identifier."""
        table = self._table("tb_strategist_signals")
        statement = (
            insert(table)
            .values(
                trade_date=value.trade_date,
                ticker=value.ticker,
                cycle_ts=value.cycle_ts,
                inv_type=value.inv_type,
                side=value.side,
                conviction=value.conviction,
                signal_consensus=value.signal_consensus,
                summary=value.summary,
                evidence=list(value.evidence),
                sizing_hint={},
                decision_close=value.decision_close,
                current_price=value.decision_close,
                day_high=value.decision_close,
                day_low=value.decision_close,
                close_prev=value.decision_close,
                volume=0,
                turnover=0,
                high_52w=value.decision_close,
                low_52w=value.decision_close,
            )
            .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts", "inv_type"])
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        select(table.c.id).where(
                            table.c.ticker == value.ticker,
                            table.c.cycle_ts == value.cycle_ts,
                            table.c.inv_type == value.inv_type,
                        )
                    )
                }
            ).value

    async def save_source_records(
        self,
        value: StrategistSignalWrite,
        disclosure_source: DisclosureSourceRecord,
        news_source: NewsSourceRecord,
    ) -> None:
        """Delegate the atomic source transaction to its focused module."""
        tables = (
            self._table("tb_disclosure"),
            self._table("tb_news"),
            self._table("tb_disclosure_signal"),
            self._table("tb_news_signal"),
        )
        await save_source_records(self._engine, tables, value, disclosure_source, news_source)

    async def save_verdict(self, value: CriticVerdictWrite) -> int:
        """Insert or reuse the unique critic verdict for a signal."""
        table = self._table("tb_critic_verdict")
        fields = {
            "signal_id": value.signal_id,
            "ticker": value.ticker,
            "decision": value.decision,
            "is_agreed": value.decision == "pass",
            "category": value.category,
            "objection": value.objection,
            "confidence": value.confidence,
            "decided_layer": value.decided_layer,
            "verdict_source": value.verdict_source,
        }
        statement = (
            insert(table).values(**fields).on_conflict_do_nothing(index_elements=["signal_id"])
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        select(table.c.id).where(table.c.signal_id == value.signal_id)
                    )
                }
            ).value

    async def save_account(self, value: AccountWrite) -> int:
        """Initialize one local account once without resetting durable balances."""
        return await initialize_account(self._engine, self._table("tb_account"), value)

    async def initialize_local_account(self, opening_cash: Decimal) -> int:
        """Initialize the fixed app-owned local account identity once."""
        account = AccountWrite(
            "quantinue-local-simulated", opening_cash, opening_cash, opening_cash
        )
        return await self.save_account(account)

    async def record_completed_fill(self, value: CompletedFillWrite) -> int:
        """Insert one unique local buy fill and debit its account atomically."""
        return await record_completed_fill(
            self._engine,
            self._table("tb_order"),
            self._table("tb_fill"),
            self._table("tb_account"),
            value,
        )

    async def reconcile_order(self, value: OrderReconciliation) -> int:
        """Update the pre-reserved order by stable idempotency key."""
        table = self._table("tb_order")
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(table.c.id, table.c.status)
                        .where(table.c.idempotency_key == value.idempotency_key)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            order_id = _IdentifierRow.model_validate({"value": row["id"]}).value
            target_status = AppOrderExposureStatus(value.status)
            if (
                row["status"]
                in {
                    AppOrderExposureStatus.FILLED.value,
                    AppOrderExposureStatus.FAILED.value,
                    AppOrderExposureStatus.CANCELED.value,
                }
                and row["status"] != target_status.value
            ):
                return order_id
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        table.update()
                        .where(table.c.id == order_id)
                        .values(
                            status=target_status.value,
                            broker_order_id=value.broker_order_id,
                            parent_order_id=value.parent_order_id,
                            stop_leg_order_id=value.stop_leg_order_id,
                            take_profit_leg_order_id=value.take_profit_leg_order_id,
                        )
                        .returning(table.c.id)
                    )
                }
            ).value

    @property
    def engine(self) -> AsyncEngine:
        """Expose the engine for operational scripts and tests."""
        return self._engine

    def _table(self, name: str) -> Table:
        return self._metadata.tables[name]
