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

**실측으로 확인한 것** (2026-07-19, 실 API 호출):
- 한 요청에 400종목까지 200 OK. 배치 크기는 병목이 아니었다.
- 주식 클래스 구분자는 **점**이다: ``BRK.B``는 200, ``BRK/B``는 400. 상장
  피드(NASDAQ)는 슬래시로 주므로 요청 시 변환이 필요하다.
- **알 수 없는 심볼 하나가 배치 전체를 400으로 죽인다**(``invalid symbol: X``).
  피드에는 실제로 쓰레기 행이 섞여 들어온다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx as httpx2

from quantinue.db.domain_records import DailyBarWrite
from quantinue.market_data.symbols import to_venue_symbol as _to_venue_symbol

_BARS_URL: Final = "https://data.alpaca.markets/v2/stocks/bars"
_SOURCE: Final = "alpaca-iex"
# 문서에 종목 수 상한이 없으므로 URL 길이로 스스로 제한한다. 티커 평균 5자 기준
# 200개면 쿼리스트링이 ~1.2KB로, 흔한 8KB 서버 상한에 한참 못 미친다.
_DEFAULT_SYMBOLS_PER_REQUEST: Final = 200
_MAX_POINTS_PER_PAGE: Final = 10_000
_INVALID_SYMBOL_PREFIX: Final = "invalid symbol: "


def _invalid_symbol(response: httpx2.Response) -> str | None:
    """Return the symbol the venue rejected, when that is why it said 400.

    모든 400을 삼키면 진짜 고장(잘못된 날짜 범위 등)이 "봉이 없네"로 위장된다.
    거부된 심볼 이름이 본문에 있을 때만 회복을 시도한다.
    """
    if response.status_code != httpx2.codes.BAD_REQUEST:
        return None
    try:
        message = str(response.json().get("message", ""))
    except ValueError:
        return None
    if not message.startswith(_INVALID_SYMBOL_PREFIX):
        return None
    return message[len(_INVALID_SYMBOL_PREFIX) :].strip() or None


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
        return await self.daily_bars_range(trade_date, trade_date, tickers)

    async def daily_bars_range(
        self, start: date, end: date, tickers: tuple[str, ...]
    ) -> tuple[DailyBarWrite, ...]:
        """Return every bar these symbols had between two dates, inclusive.

        창을 날짜별로 쪼개 부르지 않는 이유: ``start``/``end``는 같은 요청의
        파라미터라 260일 이력도 요청 수는 하루치와 같다. 늘어나는 것은 응답
        크기뿐이고, 그건 이미 페이지네이션이 감당한다(``limit``은 종목당이
        아니라 **전체 데이터 포인트** 기준이라 창이 넓어지면 페이지가 는다).
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
                collected.extend(await self._collect_chunk(client, start, end, chunk))
        return tuple(collected)

    def _chunks(self, tickers: tuple[str, ...]) -> list[tuple[str, ...]]:
        """Split symbols so no single URL grows unbounded."""
        size = max(1, self.symbols_per_request)
        return [tuple(tickers[index : index + size]) for index in range(0, len(tickers), size)]

    async def _collect_chunk(
        self,
        client: httpx2.AsyncClient,
        start: date,
        end: date,
        chunk: tuple[str, ...],
    ) -> list[DailyBarWrite]:
        """Follow pagination, dropping symbols the venue refuses to recognise.

        거부된 심볼을 빼고 다시 부르는 이유: 상장 피드에 쓰레기 행이 하나
        섞이면 그 배치 전체가 400이 되고, 하루치 봉을 통째로 잃는다. 청산은
        관측이 없으면 아무것도 하지 않으므로 그 손실이 조용히 지나간다.
        루프는 심볼이 하나씩 줄어들 때만 돌므로 반드시 끝난다.
        """
        # 우리 표기 → 거래소 표기. 응답을 되돌리려면 역매핑이 필요하다.
        venue_to_ours = {_to_venue_symbol(ticker): ticker for ticker in chunk}
        bars: list[DailyBarWrite] = []
        while venue_to_ours:
            params: dict[str, str | int] = {
                "symbols": ",".join(venue_to_ours),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "feed": "iex",
                "limit": _MAX_POINTS_PER_PAGE,
            }
            page_token: str | None = None
            rejected: str | None = None
            while True:
                if page_token is not None:
                    params["page_token"] = page_token
                response = await client.get(_BARS_URL, params=params)
                rejected = _invalid_symbol(response)
                if rejected is not None:
                    break
                _ = response.raise_for_status()
                payload = response.json()
                # 하루짜리 요청이면 응답 시각이 깨져도 어느 날인지 알지만,
                # 창 요청이면 알 수 없다 — 그때는 지어내지 않고 버린다.
                bars.extend(_parse_page(payload, start if start == end else None))
                page_token = payload.get("next_page_token")
                if not page_token:
                    return [
                        replace(bar, ticker=venue_to_ours.get(bar.ticker, bar.ticker))
                        for bar in bars
                    ]
            if venue_to_ours.pop(rejected, None) is None:
                # 우리가 보낸 적 없는 심볼을 거부했다 — 재시도해도 같으므로
                # 조용히 도는 대신 그대로 터뜨린다.
                _ = response.raise_for_status()
            bars.clear()
        return bars


def _parse_page(payload: dict[str, Any], fallback: date | None) -> list[DailyBarWrite]:
    """Map one response page, dropping bars the ledger would reject anyway."""
    parsed: list[DailyBarWrite] = []
    for ticker, entries in (payload.get("bars") or {}).items():
        for entry in entries or ():
            bar = _parse_bar(ticker, entry, fallback)
            if bar is not None:
                parsed.append(bar)
    return parsed


def _parse_bar(
    ticker: str, entry: dict[str, Any], fallback: date | None
) -> DailyBarWrite | None:
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
    trade_date = _bar_date(entry, fallback)
    if trade_date is None:
        # 어느 날의 봉인지 모르는 행은 원장의 PK를 만들 수 없다.
        return None
    return DailyBarWrite(
        trade_date=trade_date,
        ticker=ticker,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source=_SOURCE,
    )


def _bar_date(entry: dict[str, Any], fallback: date | None) -> date | None:
    """Trust the venue's own timestamp when it parses, else the requested day."""
    raw = entry.get("t")
    if not isinstance(raw, str):
        return fallback
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return fallback
