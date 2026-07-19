"""Whole-market news collection from Alpaca's news API.

**왜 일괄인가.** 공시(``sec_daily_index``)와 같은 이유다 — 종목별 폴링은 콜 수가
종목 수에 비례해서 분석 범위 밖 종목을 영영 못 본다. 이 엔드포인트는 심볼을
지정하지 않으면 **전 시장**을 주고 기사마다 ``symbols`` 배열이 붙는다.

**실 API로 확인한 계약** (2026-07-20, 우리 자격증명으로 200):
- ``GET https://data.alpaca.markets/v1beta1/news`` · ``start``/``end`` RFC3339
- ``limit`` **최대 50**. 100·1000은 400을 받는다 — 그래서 페이지네이션이
  선택이 아니다(``page_token``/``next_page_token``).
- 응답: ``id``(정수) · ``headline`` · ``summary`` · ``created_at``/``updated_at``
  · ``url`` · ``source`` · ``symbols[]``
- 실측 규모: 2026-07-17 개장 ~ 07-21 창에서 **16페이지 / 767기사 / 1440행 /
  14.5초**. 종목 필터 없이 전부 적재해도 원장이 감당하는 크기라, 무엇을 볼지
  미리 정하지 않는다 — 미리 정하면 그날 새로 든 종목이 사각지대에 남는다.

**분당 호출 한도는 여전히 미확인**이다(봉 어댑터와 같음). 추정해서 박지 않는다.

**출처 다양성은 없다.** 실측 5건·767건 모두 ``source=benzinga``다. 그래서 뉴스는
투표(``news_score``)가 아니라 **증거 종합의 맥락**으로 들어간다 —
``news_trust_policy.yaml``에서 benzinga는 gray(0.50)이고
``gates.source_trust_min``은 0.55라, 투표로 넣으면 role_07이 그 표를 통째로
박탈한다. 정책을 데이터 소스 편의로 내리는 것은 정책 오염이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

import httpx as httpx2

from quantinue.db.domain_records import RawNewsWrite
from quantinue.market_data.symbols import from_venue_symbol

if TYPE_CHECKING:
    from datetime import date

_NEWS_URL: Final = "https://data.alpaca.markets/v1beta1/news"
# 문서·실측 모두 50이 상한이다. 넘기면 400이라 "많이 받아 페이지를 줄이는"
# 최적화 자체가 불가능하다.
_MAX_PAGE_SIZE: Final = 50
# 창은 거래소 시간으로 자른다. UTC로 자르면 전날 저녁 기사(= UTC 다음 날 새벽)가
# 엉뚱한 세션에 붙는다 — 실측 기사 상당수가 장 마감 후 시간대에 몰려 있다.
_EXCHANGE_TZ: Final = ZoneInfo("America/New_York")
_ONE_DAY: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class AlpacaNewsSource:
    """Collect the whole market's headlines for one session's window."""

    key_id: str
    secret_key: str
    transport: httpx2.AsyncBaseTransport | None = None
    page_size: int = _MAX_PAGE_SIZE
    timeout_seconds: float = 30.0

    async def articles(self, session: date, until: date) -> tuple[RawNewsWrite, ...]:
        """Return every article published between the session and the run day.

        창이 하루가 아니라 **세션 시작 ~ 실행일 끝**인 이유: 잡은 보통 개장 전에
        돌고 직전 세션을 본다. 하루로 자르면 주말·휴장에 나온 기사와 실행 당일
        프리마켓 기사가 창 밖으로 떨어지는데, 다음 실행의 창은 더 뒤에서
        시작하므로 **아무도 그것을 다시 줍지 않는다**. 겹치게 받는 쪽이 안전하고,
        겹침은 (기사, 티커) 키가 흡수한다.

        ``trade_date``는 세션이다 — 이 행들이 "어느 세션의 증거인가"를 말해야
        분석 잡이 공시와 같은 축으로 읽을 수 있다.
        """
        start = datetime.combine(session, time(), tzinfo=_EXCHANGE_TZ)
        end = datetime.combine(until + _ONE_DAY, time(), tzinfo=_EXCHANGE_TZ)
        collected: list[RawNewsWrite] = []
        params: dict[str, str | int] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": max(1, min(self.page_size, _MAX_PAGE_SIZE)),
        }
        async with httpx2.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_seconds,
            # 자격증명은 헤더로만 — URL에 실으면 로그·프록시에 그대로 남는다.
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
            },
        ) as client:
            page_token: str | None = None
            while True:
                if page_token is not None:
                    params["page_token"] = page_token
                response = await client.get(_NEWS_URL, params=params)
                # 상태 코드로 "뉴스 없음"을 추정하지 않는다. 삼키면 한도 초과가
                # "그날 조용했다"로 위장되고, 판단이 조용히 얇아진다.
                _ = response.raise_for_status()
                payload = response.json()
                collected.extend(_parse_page(payload, session))
                page_token = payload.get("next_page_token")
                if not page_token:
                    return tuple(collected)


def _parse_page(payload: dict[str, Any], session: date) -> list[RawNewsWrite]:
    """Fan one page of articles out into one row per ticker."""
    rows: list[RawNewsWrite] = []
    for article in payload.get("news") or ():
        rows.extend(_parse_article(article, session))
    return rows


def _parse_article(article: dict[str, Any], session: date) -> list[RawNewsWrite]:
    """Build the ledger rows for one article, or none if it names no ticker.

    어느 종목의 증거도 아닌 기사는 우리가 쓸 자리가 없다(시장 전체 논평·매크로).
    ``headline``이 비어 있는 기사도 마찬가지다 — 프롬프트에 빈 줄을 넣으면
    모델이 "무언가 있었다"로 읽는다.
    """
    try:
        article_id = int(article["id"])
        headline = str(article["headline"]).strip()
    except (KeyError, TypeError, ValueError):
        return []
    symbols = article.get("symbols") or ()
    if not headline or not symbols:
        return []
    published_at = _published_at(article)
    source = str(article.get("source") or "unknown")
    url = str(article.get("url") or "")
    return [
        RawNewsWrite(
            article_id=article_id,
            ticker=from_venue_symbol(str(symbol)).upper(),
            trade_date=session,
            headline=headline,
            source=source,
            url=url,
            published_at=published_at,
        )
        for symbol in symbols
    ]


def _published_at(article: dict[str, Any]) -> datetime:
    """Trust the venue's own timestamp when it parses.

    못 읽으면 세션 시작으로 떨어뜨리지 않고 지금 시각을 쓴다 — 정렬(최신순
    예산 절단)에서 파싱 실패한 기사가 통째로 뒤로 밀려 영영 안 읽히는 것보다,
    수집 시점으로 두는 편이 덜 거짓말이다.
    """
    raw = article.get("created_at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC)
