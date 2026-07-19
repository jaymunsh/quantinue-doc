"""Whole-market disclosure collection from EDGAR's daily index.

**왜 일괄인가.** 기존 경로(role_05)는 종목당 ``data.sec.gov/submissions/CIK*.json``
1콜이라 콜 수가 종목 수에 비례했다. 그래서 분석 대상 밖 종목은 아예 못 봤고,
정작 급한 것 — **스크리너에서 탈락한 보유 종목의 상장폐지 공시** — 이 사각지대에
있었다. 일일 인덱스는 그날 전체 제출분을 파일 하나로 주므로 콜 수가 종목 수와
무관해진다.

**문서·실물로 확인한 계약** (2026-07-19):
- ``https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{N}/form.{YYYYMMDD}.idx``
- 고정폭 텍스트다. 공백 분할은 쓸 수 없다 — 폼 타입에 공백이 들어간다
  (``SCHEDULE 13D``). 실측 컬럼 경계: 0/16/78/90/102.
- 하루 3000~4100행, 75KB 안팎. 그중 상장 티커로 매칭되는 것은 일부다.
- 휴장일·공휴일에는 파일이 없고, 그때 SEC는 404가 아니라 **403**을 준다
  (2026-07-18 토, 2026-01-01 모두 403 / 2026-07-17 금은 200). 그런데 403은
  User-Agent 정책 차단과 구분되지 않는다 — 삼키면 진짜 차단이 "공시 0건"으로
  위장된다. 그래서 여기서는 아무것도 삼키지 않고, **거래일만 묻는 책임을
  호출자(잡)에게 둔다**. 인덱스가 아직 안 올라온 경우에도 실패로 남는 편이
  낫다 — 잡 원장이 실패를 기록하고 다음 틱에서 재시도한다.
- ``www.sec.gov``는 연락처가 담긴 User-Agent를 요구한다(``sec_user_agent``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import anyio
import httpx as httpx2

from quantinue.db.domain_records import RawDisclosureWrite
from quantinue.market_data.http_client import sec_user_agent

if TYPE_CHECKING:
    from datetime import date

    from quantinue.core.ontology import EventType

_INDEX_URL: Final = (
    "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day}.idx"
)
_TICKER_MAP_URL: Final = "https://www.sec.gov/files/company_tickers.json"

# 실측한 고정폭 경계. 헤더가 두 줄로 접혀 있어 헤더 파싱으로는 못 얻는다.
_FORM_TYPE: Final = slice(0, 16)
_COMPANY_NAME: Final = slice(16, 78)
_CIK: Final = slice(78, 90)
_FILE_NAME: Final = slice(102, None)
_SEPARATOR_PREFIX: Final = "---"

# 상장폐지·등록말소 폼. 이건 "논지가 흔들린다"가 아니라 **사실**이라서 LLM을
# 거치지 않고 즉시 청산으로 간다(재설계 §4 1c ③). 목록을 좁게 유지하는 게
# 중요하다 — 평범한 공시가 하나라도 섞이면 시스템이 아무 때나 판다.
#   25 · 25-NSE  상장 폐지 통지(발행사/거래소)
#   15-12B · 15-12G · 15-15D  등록 말소·보고 의무 종료
_HARD_EVENT_FORMS: Final = frozenset(
    {"25", "25-NSE", "15-12B", "15-12G", "15-15D"}
)


def hard_event_for_form(form_type: str) -> EventType | None:
    """Return the hard event this SEC form type announces, if any."""
    if form_type.strip().upper() in _HARD_EVENT_FORMS:
        return "delisting_halt"
    return None


@dataclass(frozen=True, slots=True)
class IndexRow:
    """One raw line of the daily form index."""

    form_type: str
    company_name: str
    cik: str
    filing_no: str
    source_ref: str


def parse_form_index(text: str) -> tuple[IndexRow, ...]:
    """Parse the fixed-width daily index into rows.

    구분선(``---``) 다음부터가 데이터다. 헤더 블록에 날짜·연락처가 섞여 있어
    "숫자로 시작하면 데이터" 같은 휴리스틱은 오작동한다.
    """
    lines = text.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.startswith(_SEPARATOR_PREFIX)
        ),
        None,
    )
    if start is None:
        return ()
    rows: list[IndexRow] = []
    for line in lines[start:]:
        source_ref = line[_FILE_NAME].strip()
        cik = line[_CIK].strip()
        if not source_ref or not cik:
            continue
        # 접수번호(accession)는 경로 끝 파일명이다. 같은 날 같은 종목이 여러 건을
        # 낼 수 있으므로 (날짜, 티커)로는 행을 구분할 수 없다.
        filing_no = source_ref.rsplit("/", 1)[-1].removesuffix(".txt")
        rows.append(
            IndexRow(
                form_type=line[_FORM_TYPE].strip(),
                company_name=line[_COMPANY_NAME].strip(),
                cik=cik,
                filing_no=filing_no,
                source_ref=source_ref,
            )
        )
    return tuple(rows)


@dataclass(eq=False)
class SecDailyIndexSource:
    """Collect one session's filings for the whole market in a single request."""

    transport: httpx2.AsyncBaseTransport | None = None
    timeout_seconds: float = 30.0
    _cik_to_ticker: dict[str, str] | None = field(default=None, init=False)
    _lock: anyio.Lock = field(default_factory=anyio.Lock, init=False)

    async def filings(self, trade_date: date) -> tuple[RawDisclosureWrite, ...]:
        """Return the day's filings that map to a listed ticker.

        티커로 매칭되지 않는 행은 버린다 — 펀드·SPV·비상장 발행사가 대다수이고,
        우리가 판단할 수 있는 대상이 아니다. 원장을 작게 유지하는 효과도 있다.
        """
        async with httpx2.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_seconds,
            headers={"User-Agent": sec_user_agent()},
        ) as client:
            cik_to_ticker = await self._ticker_map(client)
            url = _INDEX_URL.format(
                year=trade_date.year,
                quarter=(trade_date.month - 1) // 3 + 1,
                day=trade_date.strftime("%Y%m%d"),
            )
            response = await client.get(url)
            # 상태 코드로 "없음"을 추정하지 않는다 — 위 모듈 주석 참조.
            _ = response.raise_for_status()
            rows = parse_form_index(response.text)
        collected: list[RawDisclosureWrite] = []
        for row in rows:
            ticker = cik_to_ticker.get(row.cik.lstrip("0"))
            if ticker is None:
                continue
            event_type = hard_event_for_form(row.form_type)
            collected.append(
                RawDisclosureWrite(
                    filing_no=row.filing_no,
                    trade_date=trade_date,
                    ticker=ticker,
                    cik=row.cik,
                    form_type=row.form_type,
                    company_name=row.company_name,
                    source_ref=row.source_ref,
                    event_type=event_type,
                    is_hard_event=event_type is not None,
                )
            )
        return tuple(collected)

    async def _ticker_map(self, client: httpx2.AsyncClient) -> dict[str, str]:
        """Fetch the CIK→ticker map once and reuse it."""
        async with self._lock:
            if self._cik_to_ticker is None:
                response = await client.get(_TICKER_MAP_URL)
                _ = response.raise_for_status()
                # 인덱스의 CIK는 0 패딩이 없고 맵은 정수다 — 양쪽을 문자열
                # 정규형으로 맞춰야 조인이 조용히 비지 않는다.
                self._cik_to_ticker = {
                    str(entry["cik_str"]): str(entry["ticker"]).upper()
                    for entry in response.json().values()
                }
            return self._cik_to_ticker
