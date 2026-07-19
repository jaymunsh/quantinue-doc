"""Phase 2: batch daily-bar collection against the documented Alpaca contract."""

from datetime import date
from decimal import Decimal

import httpx as httpx2
import pytest

from quantinue.market_data.alpaca_bars import AlpacaBarSource

_DAY = date(2026, 7, 8)


def _page(bars: dict[str, list[dict[str, object]]], token: str | None = None) -> dict[str, object]:
    return {"bars": bars, "next_page_token": token}


def _bar(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "t": "2026-07-08T04:00:00Z",
        "o": 100.0,
        "h": 110.0,
        "l": 95.0,
        "c": 105.0,
        "v": 1_000_000,
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_one_request_carries_every_symbol() -> None:
    """배치가 이 어댑터의 존재 이유다 — 종목당 1콜이면 500콜로 돌아간다."""
    # Given
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_page({"AAA": [_bar()], "BBB": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA", "BBB"))

    # Then
    assert len(seen) == 1
    assert seen[0].url.params["symbols"] == "AAA,BBB"
    assert seen[0].url.params["timeframe"] == "1Day"
    assert seen[0].url.params["feed"] == "iex"
    assert {bar.ticker for bar in bars} == {"AAA", "BBB"}


@pytest.mark.anyio
async def test_credentials_travel_in_headers_not_the_url() -> None:
    """키가 URL에 실리면 로그·프록시에 그대로 남는다."""
    # Given
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_page({"AAA": [_bar()]}))

    source = AlpacaBarSource(
        key_id="key-1", secret_key="secret-1", transport=httpx2.MockTransport(handler)
    )

    # When
    _ = await source.daily_bars(_DAY, ("AAA",))

    # Then
    assert seen[0].headers["APCA-API-KEY-ID"] == "key-1"
    assert "secret-1" not in str(seen[0].url)


@pytest.mark.anyio
async def test_pagination_is_followed_until_the_token_runs_out() -> None:
    """limit은 종목당이 아니라 전체 포인트 기준이라(문서) 큰 유니버스는 반드시 쪼개진다."""
    # Given
    pages = [
        _page({"AAA": [_bar()]}, token="more"),
        _page({"BBB": [_bar()]}, token=None),
    ]
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200, json=pages[len(calls) - 1])

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA", "BBB"))

    # Then
    assert len(calls) == 2
    assert calls[1].url.params["page_token"] == "more"
    assert {bar.ticker for bar in bars} == {"AAA", "BBB"}


@pytest.mark.anyio
async def test_ohlcv_is_mapped_to_the_ledger_shape() -> None:
    # Given
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_page({"AAA": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA",))

    # Then
    bar = bars[0]
    assert bar.trade_date == _DAY
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal("100.0"),
        Decimal("110.0"),
        Decimal("95.0"),
        Decimal("105.0"),
    )
    assert bar.volume == 1_000_000
    assert bar.source == "alpaca-iex"


@pytest.mark.anyio
async def test_a_bar_whose_range_is_impossible_is_dropped_not_stored() -> None:
    """low > high인 봉은 DB CHECK에 걸려 적재 전체를 죽인다 — 여기서 거른다."""
    # Given
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json=_page({"AAA": [_bar(l=120.0, h=110.0)], "BBB": [_bar()]})
        )

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA", "BBB"))

    # Then: 나쁜 봉 하나가 좋은 봉들을 버리게 두지 않는다
    assert {bar.ticker for bar in bars} == {"BBB"}


@pytest.mark.anyio
async def test_a_symbol_absent_from_the_response_is_simply_absent() -> None:
    """상장폐지·거래정지 종목은 응답에 없다 — 지어내면 청산이 가짜로 돈다."""
    # Given
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_page({"AAA": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA", "GONE"))

    # Then
    assert {bar.ticker for bar in bars} == {"AAA"}


@pytest.mark.anyio
async def test_symbols_are_chunked_to_keep_the_url_bounded() -> None:
    """수천 종목을 한 URL에 넣으면 서버가 414로 끊는다."""
    # Given
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        symbols = str(request.url.params["symbols"]).split(",")
        return httpx2.Response(
            200, json=_page({symbol: [_bar()] for symbol in symbols})
        )

    source = AlpacaBarSource(
        key_id="k",
        secret_key="s",
        transport=httpx2.MockTransport(handler),
        symbols_per_request=100,
    )

    # When
    bars = await source.daily_bars(_DAY, tuple(f"T{index}" for index in range(250)))

    # Then
    assert len(calls) == 3
    assert len(bars) == 250


@pytest.mark.anyio
async def test_share_class_symbols_are_sent_in_the_venue_spelling() -> None:
    """상장 피드는 BRK/B로 주고 Alpaca는 BRK.B만 받는다 — 실측 확인."""
    # Given
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_page({"BRK.B": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("BRK/B",))

    # Then: 요청은 점, 원장은 우리 표기(슬래시) — 조인이 깨지면 안 된다
    assert seen[0].url.params["symbols"] == "BRK.B"
    assert [bar.ticker for bar in bars] == ["BRK/B"]


@pytest.mark.anyio
async def test_one_bad_symbol_does_not_lose_the_whole_batch() -> None:
    """쓰레기 심볼 하나에 2000종목 수집이 통째로 죽으면 하루를 잃는다."""
    # Given
    attempts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        symbols = str(request.url.params["symbols"])
        attempts.append(symbols)
        if "E90F6115D" in symbols:
            return httpx2.Response(400, json={"message": "invalid symbol: E90F6115D"})
        return httpx2.Response(200, json=_page({"AAA": [_bar()], "BBB": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("AAA", "E90F6115D", "BBB"))

    # Then: 나쁜 심볼만 떨어지고 나머지는 살아 돌아온다
    assert sorted(bar.ticker for bar in bars) == ["AAA", "BBB"]
    assert attempts[-1] == "AAA,BBB"


@pytest.mark.anyio
async def test_several_bad_symbols_are_dropped_one_by_one() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        symbols = str(request.url.params["symbols"])
        for bad in ("BAD1", "BAD2"):
            if bad in symbols:
                return httpx2.Response(400, json={"message": f"invalid symbol: {bad}"})
        return httpx2.Response(200, json=_page({"AAA": [_bar()]}))

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When
    bars = await source.daily_bars(_DAY, ("BAD1", "AAA", "BAD2"))

    # Then
    assert [bar.ticker for bar in bars] == ["AAA"]


@pytest.mark.anyio
async def test_a_400_that_is_not_about_a_symbol_still_fails_loudly() -> None:
    """모든 400을 삼키면 진짜 고장이 '봉이 없네'로 위장된다."""
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"message": "end must be after start"})

    source = AlpacaBarSource(
        key_id="k", secret_key="s", transport=httpx2.MockTransport(handler)
    )

    # When / Then
    with pytest.raises(httpx2.HTTPStatusError):
        _ = await source.daily_bars(_DAY, ("AAA",))
