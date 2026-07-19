"""Batch daily-bar collection from Alpaca's market data API.

**왜 배치인가.** 기존 경로(role_02)는 종목당 1콜을 동시성 10으로 돌려 500종목에
타임아웃 900초를 잡아뒀다. Alpaca의 ``/v2/stocks/bars``는 ``symbols``에 쉼표로
여러 종목을 받으므로, 일일 증분(종목당 1봉)은 요청 한두 건으로 끝난다. 커버리지를
넓히면서 콜 수를 줄이는 게 Phase 2 데이터층의 핵심이다.

**문서로 확인한 계약** (2026-07-19, docs.alpaca.markets/us/reference/stockbars):
- ``GET https://data.alpaca.markets/v2/stocks/bars``
- ``symbols`` 쉼표 구분. **종목 수 상한은 문서화돼 있지 않다** — 그래서 URL 길이가
  터지지 않을 만큼만 우리가 스스로 쪼갠다(``symbols_per_request``).
- ``timeframe=1Day`` · ``start``/``end``는 ``YYYY-MM-DD`` 허용
- ``feed``: ``sip``(기본)·``iex``·``boats``·``otc``. 무료 플랜은 **iex**.
- ``limit`` 최대 10000이며 **"종목당이 아니라 전체 데이터 포인트 기준"**(문서 원문)
  → 페이지네이션(``page_token``/``next_page_token``)은 선택이 아니라 필수다.

**분당 호출 한도는 공식 문서에서 확인하지 못했다.** 추정해서 박아두지 않고
요청 크기만 설정값으로 열어둔다 — 실측 후 조이는 편이 안전하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx as httpx2

from quantinue.db.domain_records import DailyBarWrite

_BARS_URL: Final = "https://data.alpaca.markets/v2/stocks/bars"
_SOURCE: Final = "alpaca-iex"
# 문서에 종목 수 상한이 없으므로 URL 길이로 스스로 제한한다. 티커 평균 5자 기준
# 200개면 쿼리스트링이 ~1.2KB로, 흔한 8KB 서버 상한에 한참 못 미친다.
_DEFAULT_SYMBOLS_PER_REQUEST: Final = 200
_MAX_POINTS_PER_PAGE: Final = 10_000


@dataclass(frozen=True, slots=True)
class AlpacaBarSource:
    """Collect one session's bars for many symbols with as few requests as possible."""

    key_id: str
    secret_key: str
    transport: httpx2.AsyncBaseTransport | None = None
    symbols_per_request: int = _DEFAULT_SYMBOLS_PER_REQUEST
    timeout_seconds: float = 30.0

    async def daily_bars(
        self, trade_date: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        """Return every bar the venue had for these symbols on this date.

        응답에 없는 종목은 **그냥 없다**. 상장폐지·거래정지·신규상장 전이면
        봉이 없는 게 정상이고, 여기서 0이나 전일 값으로 채우면 청산 잡이
        가짜 관측을 근거로 판단하게 된다.
        """
        if not tickers:
            return ()
        collected: list[DailyBarWrite] = []
        async with httpx2.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_seconds,
            # 자격증명은 헤더로만. URL에 실으면 로그·프록시·에러 리포트에
            # 그대로 남는다.
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
            },
        ) as client:
            for chunk in self._chunks(tickers):
                collected.extend(await self._collect_chunk(client, trade_date, chunk))
        return tuple(collected)

    def _chunks(self, tickers: tuple[str, ...]) -> list[tuple[str, ...]]:
        """Split symbols so no single URL grows unbounded."""
        size = max(1, self.symbols_per_request)
        return [tuple(tickers[index : index + size]) for index in range(0, len(tickers), size)]

    async def _collect_chunk(
        self,
        client: httpx2.AsyncClient,
        trade_date: date,
        chunk: tuple[str, ...],
    ) -> list[DailyBarWrite]:
        """Follow pagination until the venue stops handing back a token."""
        day = trade_date.isoformat()
        params: dict[str, str | int] = {
            "symbols": ",".join(chunk),
            "timeframe": "1Day",
            "start": day,
            "end": day,
            "feed": "iex",
            "limit": _MAX_POINTS_PER_PAGE,
        }
        bars: list[DailyBarWrite] = []
        page_token: str | None = None
        while True:
            if page_token is not None:
                params["page_token"] = page_token
            response = await client.get(_BARS_URL, params=params)
            _ = response.raise_for_status()
            payload = response.json()
            bars.extend(_parse_page(payload, trade_date))
            page_token = payload.get("next_page_token")
            if not page_token:
                return bars


def _parse_page(payload: dict[str, Any], trade_date: date) -> list[DailyBarWrite]:
    """Map one response page, dropping bars the ledger would reject anyway."""
    parsed: list[DailyBarWrite] = []
    for ticker, entries in (payload.get("bars") or {}).items():
        for entry in entries or ():
            bar = _parse_bar(ticker, entry, trade_date)
            if bar is not None:
                parsed.append(bar)
    return parsed


def _parse_bar(ticker: str, entry: dict[str, Any], trade_date: date) -> DailyBarWrite | None:
    """Build one ledger row, or None when the venue handed us something impossible.

    나쁜 봉 하나가 적재 전체를 죽이지 않게 여기서 거른다. DB의 정합성 CHECK를
    믿고 그냥 넣으면 500종목 배치가 한 종목 때문에 통째로 롤백된다.
    """
    try:
        open_ = Decimal(str(entry["o"]))
        high = Decimal(str(entry["h"]))
        low = Decimal(str(entry["l"]))
        close = Decimal(str(entry["c"]))
        volume = int(entry["v"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if min(open_, high, low, close) <= 0 or volume < 0:
        return None
    if not (low <= open_ <= high and low <= close <= high):
        return None
    return DailyBarWrite(
        trade_date=_bar_date(entry, trade_date),
        ticker=ticker,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source=_SOURCE,
    )


def _bar_date(entry: dict[str, Any], fallback: date) -> date:
    """Trust the venue's own timestamp when it parses, else the requested day."""
    raw = entry.get("t")
    if not isinstance(raw, str):
        return fallback
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return fallback
