"""Phase 3: one request stream per day for the whole market's headlines."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import httpx as httpx2
import pytest

from quantinue.market_data.alpaca_news import AlpacaNewsSource

if TYPE_CHECKING:
    from collections.abc import Callable

_SESSION = date(2026, 7, 17)
_UNTIL = date(2026, 7, 20)


def _article(article_id: int, symbols: list[str], headline: str = "h") -> dict[str, Any]:
    return {
        "id": article_id,
        "headline": headline,
        "summary": "",
        "author": "Webmaster",
        "created_at": "2026-07-17T23:50:00Z",
        "updated_at": "2026-07-17T23:50:00Z",
        "source": "benzinga",
        "url": f"https://www.benzinga.com/news/{article_id}",
        "symbols": symbols,
    }


def _source(handler: Callable[[httpx2.Request], httpx2.Response]) -> AlpacaNewsSource:
    return AlpacaNewsSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )


@pytest.mark.anyio
async def test_one_article_becomes_one_row_per_symbol() -> None:
    """한 기사가 여러 종목을 언급한다 — 종목별로 읽히려면 행도 종목별이어야 한다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json={"news": [_article(1, ["AAA", "BBB"])]})

    # When
    rows = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert [(row.article_id, row.ticker) for row in rows] == [(1, "AAA"), (1, "BBB")]


@pytest.mark.anyio
async def test_the_session_is_what_the_row_is_evidence_for() -> None:
    """공시와 같은 축으로 읽힌다 — 분석 잡은 (세션, 티커)로 증거를 찾는다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json={"news": [_article(1, ["AAA"])]})

    # When
    rows = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert rows[0].trade_date == _SESSION
    assert rows[0].published_at == datetime.fromisoformat("2026-07-17T23:50:00+00:00")
    assert rows[0].source == "benzinga"


@pytest.mark.anyio
async def test_the_window_opens_at_the_session_and_closes_after_the_run_day() -> None:
    """세션 이후 주말·당일 프리마켓 기사가 창 밖으로 떨어지면 영영 안 들어온다.

    뉴욕 시간이어야 한다 — UTC로 자르면 전날 저녁(= UTC 다음 날 새벽) 기사가
    엉뚱한 세션에 붙는다.
    """
    # Given
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(parse_qs(urlparse(str(request.url)).query))
        return httpx2.Response(200, json={"news": []})

    # When
    _ = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert seen[0]["start"] == ["2026-07-17T00:00:00-04:00"]
    assert seen[0]["end"] == ["2026-07-21T00:00:00-04:00"]


@pytest.mark.anyio
async def test_pagination_is_followed_until_the_token_runs_out() -> None:
    """실측: 4일 창이 16페이지(767건)다. 첫 페이지만 읽으면 대부분을 잃는다."""
    # Given
    pages = [
        {"news": [_article(1, ["AAA"])], "next_page_token": "t1"},
        {"news": [_article(2, ["BBB"])], "next_page_token": None},
    ]
    seen: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        seen.append((query.get("page_token") or [None])[0])
        return httpx2.Response(200, json=pages[len(seen) - 1])

    # When
    rows = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert seen == [None, "t1"]
    assert [row.article_id for row in rows] == [1, 2]


@pytest.mark.anyio
async def test_articles_without_a_symbol_are_dropped() -> None:
    """어느 종목의 증거도 아닌 기사는 우리가 판단에 쓸 수 없다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(
            200, json={"news": [_article(1, []), _article(2, ["AAA"])]}
        )

    # When
    rows = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert [row.article_id for row in rows] == [2]


@pytest.mark.anyio
async def test_the_venue_class_separator_is_translated_back_to_ours() -> None:
    """Alpaca는 BRK.B, 우리 원장은 BRK/B다. 안 되돌리면 조인이 조용히 빈다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json={"news": [_article(1, ["BRK.B"])]})

    # When
    rows = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert rows[0].ticker == "BRK/B"


@pytest.mark.anyio
async def test_a_failed_request_is_not_swallowed() -> None:
    """수집 실패가 "그날 뉴스 0건"으로 위장되면 판단이 조용히 얇아진다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(429, text="rate limited")

    # When / Then
    with pytest.raises(httpx2.HTTPStatusError):
        _ = await _source(handler).articles(_SESSION, _UNTIL)


@pytest.mark.anyio
async def test_credentials_travel_in_headers_not_the_url() -> None:
    # Given
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"news": []})

    # When
    _ = await _source(handler).articles(_SESSION, _UNTIL)

    # Then
    assert seen[0].headers["APCA-API-KEY-ID"] == "k"
    assert seen[0].headers["APCA-API-SECRET-KEY"] == "s"
    assert "APCA" not in str(seen[0].url)
