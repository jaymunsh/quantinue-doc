"""Phase 2: one call per day for the whole market's filings."""

from __future__ import annotations

from datetime import date

import httpx as httpx2
import pytest

from quantinue.market_data.sec_daily_index import (
    SecDailyIndexSource,
    hard_event_for_form,
    parse_form_index,
)

_DAY = date(2026, 7, 17)

# 실물 form.20260717.idx의 레이아웃을 그대로 재현한다. 컬럼 폭은 실측값이고,
# 여기 적힌 숫자가 곧 파서와의 계약이다 — 어긋나면 이 테스트가 먼저 깨진다.
def _row(form: str, company: str, cik: str, path: str) -> str:
    return f"{form:<16}{company:<62}{cik:<12}{'20260717':<12}{path}"


_INDEX = "\n".join(
    (
        "Description:           Daily Index of EDGAR Dissemination Feed by Form Type",
        "Last Data Received:    Jul 17, 2026",
        "",
        "Form Type   Company Name                                                  CIK",
        "      Date Filed  File Name",
        "-" * 141,
        _row(
            "25-NSE",
            "CATALYST PHARMACEUTICALS, INC.",
            "1369568",
            "edgar/data/1369568/0001354457-26-000694.txt",
        ),
        _row(
            "SCHEDULE 13D",
            "COLUMBUS CIRCLE 3 SPONSOR Corp LLC",
            "2137455",
            "edgar/data/2137455/0001185185-26-003007.txt",
        ),
        _row(
            "8-K",
            "NVIDIA CORP",
            "1045810",
            "edgar/data/1045810/0001045810-26-000123.txt",
        ),
    )
)


def test_the_index_is_parsed_by_fixed_width_not_whitespace() -> None:
    """폼 타입에 공백이 들어간다(SCHEDULE 13D) — 토큰 분할은 회사명을 먹는다."""
    # When
    rows = parse_form_index(_INDEX)

    # Then
    assert [row.form_type for row in rows] == ["25-NSE", "SCHEDULE 13D", "8-K"]
    assert rows[1].company_name == "COLUMBUS CIRCLE 3 SPONSOR Corp LLC"
    assert rows[2].cik == "1045810"


def test_the_accession_number_is_taken_from_the_file_path() -> None:
    """행마다 고유한 열쇠가 필요하다 — 같은 날 같은 종목이 여러 건 낼 수 있다."""
    # When
    rows = parse_form_index(_INDEX)

    # Then
    assert rows[0].filing_no == "0001354457-26-000694"


def test_the_header_block_is_not_mistaken_for_data() -> None:
    # When
    rows = parse_form_index(_INDEX)

    # Then
    assert len(rows) == 3


@pytest.mark.parametrize("form", ["25", "25-NSE", "15-12B", "15-12G", "15-15D"])
def test_delisting_forms_are_hard_events(form: str) -> None:
    """상장폐지·등록말소는 논지 붕괴가 아니라 사실이다 — LLM을 거치지 않는다."""
    assert hard_event_for_form(form) == "delisting_halt"


@pytest.mark.parametrize("form", ["8-K", "10-Q", "4", "SCHEDULE 13D", ""])
def test_ordinary_forms_are_not_hard_events(form: str) -> None:
    """평범한 공시를 하드 이벤트로 올리면 시스템이 아무 때나 판다."""
    assert hard_event_for_form(form) is None


def test_form_matching_ignores_case_and_padding() -> None:
    assert hard_event_for_form("  25-nse  ") == "delisting_halt"


@pytest.mark.anyio
async def test_one_request_covers_the_whole_day() -> None:
    """종목당 1콜(500콜)을 그날 1콜로 바꾸는 것이 이 어댑터의 존재 이유다."""
    # Given
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        if "company_tickers" in str(request.url):
            return httpx2.Response(
                200,
                json={
                    "0": {"cik_str": 1369568, "ticker": "CPRX", "title": "Catalyst"},
                    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "Nvidia"},
                },
            )
        return httpx2.Response(200, text=_INDEX)

    source = SecDailyIndexSource(transport=httpx2.MockTransport(handler))

    # When
    filings = await source.filings(_DAY)

    # Then
    assert any("form.20260717.idx" in url for url in seen)
    assert any("QTR3" in url for url in seen)
    assert {row.ticker for row in filings} == {"CPRX", "NVDA"}


@pytest.mark.anyio
async def test_filings_we_cannot_match_to_a_ticker_are_dropped() -> None:
    """CIK 매칭이 안 되는 공시가 대부분이다 — 펀드·SPV·비상장."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        if "company_tickers" in str(request.url):
            return httpx2.Response(
                200, json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "N"}}
            )
        return httpx2.Response(200, text=_INDEX)

    source = SecDailyIndexSource(transport=httpx2.MockTransport(handler))

    # When
    filings = await source.filings(_DAY)

    # Then
    assert [row.ticker for row in filings] == ["NVDA"]


@pytest.mark.anyio
async def test_the_hard_event_survives_into_the_ledger_record() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        if "company_tickers" in str(request.url):
            return httpx2.Response(
                200, json={"0": {"cik_str": 1369568, "ticker": "CPRX", "title": "C"}}
            )
        return httpx2.Response(200, text=_INDEX)

    source = SecDailyIndexSource(transport=httpx2.MockTransport(handler))

    # When
    filings = await source.filings(_DAY)

    # Then
    assert filings[0].is_hard_event is True
    assert filings[0].event_type == "delisting_halt"
    assert filings[0].trade_date == _DAY


@pytest.mark.anyio
async def test_a_missing_index_fails_loudly_instead_of_reporting_no_filings() -> None:
    """SEC는 없는 인덱스에 403을 주는데, 그건 UA 차단과 구분되지 않는다.

    삼키면 정책 차단이 "그날 공시 0건"으로 위장되고, 하드 이벤트가 조용히
    사라진다. 거래일만 묻는 책임은 호출자에게 있고, 여기서는 크게 실패한다 —
    잡 원장이 실패를 기록하고 다음 틱에서 재시도한다.
    """
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        if "company_tickers" in str(request.url):
            return httpx2.Response(200, json={})
        return httpx2.Response(403, text="Forbidden")

    source = SecDailyIndexSource(transport=httpx2.MockTransport(handler))

    # When / Then
    with pytest.raises(httpx2.HTTPStatusError):
        _ = await source.filings(_DAY)
