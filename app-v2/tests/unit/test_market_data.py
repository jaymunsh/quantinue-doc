"""Market-data adapters stay deterministic offline and parse wire responses."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx2
import pytest
from pydantic import ValidationError

from quantinue.broker.mock import MockBroker
from quantinue.core.config import DataMode, Settings
from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.core.errors import HttpFailureError, ValidationFailureError
from quantinue.core.ontology import Regime
from quantinue.db.store import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.market_data import (
    HTTP_CLIENT_POLICY,
    FixtureMarketData,
    HttpMarketData,
    MarketDataEndpoints,
    build_http_client,
)
from quantinue.market_data.models import TickerNewsQuery
from quantinue.orchestration.factory import build_market_data, build_roles
from quantinue.orchestration.failure_policy import classify_failure
from quantinue.orchestration.pipeline import PipelineOrchestrator
from quantinue.roles.role_01_universe_screener.service import UniverseScreener
from quantinue.roles.role_02_technical_analysis.service import TechnicalAnalysis
from quantinue.roles.role_04_macro_analysis.service import MacroAnalysis
from quantinue.roles.role_05_disclosure_analysis.service import DisclosureAnalysis
from quantinue.roles.role_06_news_analysis.service import NewsAnalysis


def test_owned_http_factory_exposes_required_transport_policy() -> None:
    # Given / When
    policy = HTTP_CLIENT_POLICY

    # Then
    assert policy.http2 is True
    assert policy.retries == 3
    assert policy.max_connections == 200
    assert policy.max_keepalive_connections == 40
    assert policy.keepalive_expiry == 30.0
    assert policy.connect_timeout == 5.0
    assert policy.read_timeout == 30.0
    assert policy.write_timeout == 10.0
    assert policy.pool_timeout == 10.0
    assert policy.tcp_nodelay == 1


@pytest.mark.anyio
async def test_fixture_returns_stable_no_key_snapshot() -> None:
    # Given
    source = FixtureMarketData()

    # When
    first = await source.screener("run-1")
    second = await source.screener("run-1")

    # Then
    assert first == second
    assert first[0].ticker == "NVDA"
    assert first[0].provenance.execution_id == "run-1"
    assert first[0].provenance.source == "fixture:nasdaq-screener"


def test_data_mode_selects_fixture_or_public_adapter() -> None:
    # Given / When
    fixture = build_market_data(Settings(data_mode=DataMode.FIXTURE))
    public = build_market_data(Settings(data_mode=DataMode.PUBLIC))

    # Then
    assert isinstance(fixture, FixtureMarketData)
    assert isinstance(public, HttpMarketData)


@pytest.mark.anyio
async def test_public_market_data_is_injected_into_all_data_roles() -> None:
    # Given
    source = HttpMarketData(
        build_http_client(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, request=request))
        ),
        MarketDataEndpoints.defaults(),
    )

    # When
    roles = build_roles(DeterministicAnalyzer(), broker=MockBroker(), market_data=source)

    # Then
    assert isinstance(roles[0], UniverseScreener)
    assert isinstance(roles[1], TechnicalAnalysis)
    assert isinstance(roles[3], MacroAnalysis)
    assert isinstance(roles[4], DisclosureAnalysis)
    assert isinstance(roles[5], NewsAnalysis)
    assert roles[0].market_data is source
    assert roles[1].market_data is source
    assert roles[3].market_data is source
    assert roles[4].market_data is source
    assert roles[5].market_data is source
    await source.aclose()


def test_explicit_fixture_provider_is_not_hidden_by_composition() -> None:
    # Given
    source = FixtureMarketData()

    # When
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), source)

    # Then
    assert isinstance(roles[0], UniverseScreener)
    assert isinstance(roles[1], TechnicalAnalysis)
    assert isinstance(roles[3], MacroAnalysis)
    assert isinstance(roles[4], DisclosureAnalysis)
    assert isinstance(roles[5], NewsAnalysis)
    assert roles[0].market_data is source
    assert roles[1].market_data is source
    assert roles[3].market_data is source
    assert roles[4].market_data is source
    assert roles[5].market_data is source


@pytest.mark.anyio
async def test_orchestrator_closes_owned_public_market_data() -> None:
    # Given
    client = build_http_client(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, request=request))
    )
    source = HttpMarketData(
        client,
        MarketDataEndpoints.defaults(),
    )
    orchestrator = PipelineOrchestrator((), InMemoryRunStore())
    orchestrator.own_resource(source)

    # When
    await orchestrator.close()

    # Then
    assert client.is_closed is True


def test_public_defaults_require_no_secret_query_parameters() -> None:
    # Given / When
    endpoints = MarketDataEndpoints.defaults()

    # Then
    assert "apikey" not in endpoints.candles_url.lower()
    assert "api_key" not in endpoints.macro_url.lower()


@pytest.mark.anyio
async def test_http_adapters_parse_public_feeds_at_wire_boundary() -> None:
    # Given
    observed = datetime(2026, 7, 10, 20, tzinfo=UTC)
    responses = {
        "/screener": httpx2.Response(
            200,
            json={
                "data": {
                    "table": {
                        "rows": [
                            {
                                "symbol": "nvda",
                                "name": "NVIDIA",
                                "marketCap": "1,000",
                                "lastsale": "$150.25",
                                "volume": "4,200",
                            }
                        ]
                    }
                }
            },
        ),
        "/candles/NVDA": httpx2.Response(
            200,
            json={
                "data": {
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "07/10/2026",
                                "open": "$149",
                                "high": "$152",
                                "low": "$148",
                                "close": "$151",
                                "volume": "100",
                            }
                        ]
                    }
                },
                "status": {"rCode": 200},
            },
        ),
        "/macro": httpx2.Response(
            200, json={"observations": [{"date": "2026-07-10", "value": "4.25"}]}
        ),
        "/sec/0001045810.json": httpx2.Response(
            200,
            json={
                "cik": "0001045810",
                "name": "NVIDIA CORP",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0001"],
                        "filingDate": ["2026-07-10"],
                        "form": ["8-K"],
                        "primaryDocument": ["x.htm"],
                    }
                },
            },
        ),
        "/feed.xml": httpx2.Response(
            200,
            text=(
                "<rss><channel><item><title>NVIDIA update</title>"
                "<link>https://example.test/nvda</link>"
                "<description>Short snippet</description>"
                "<pubDate>Fri, 10 Jul 2026 20:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
        ),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return responses[request.url.path]

    client = build_http_client(transport=httpx2.MockTransport(handler))
    endpoints = MarketDataEndpoints(
        screener_url="https://wire.test/screener",
        candles_url="https://wire.test/candles/{ticker}",
        macro_url="https://wire.test/macro",
        sec_url="https://wire.test/sec/{cik}.json",
        rss_url="https://wire.test/feed.xml",
    )

    # When
    source = HttpMarketData(client, endpoints, clock=lambda: observed)
    universe = await source.screener("run-2")
    candles = await source.candles("NVDA", "run-2")
    macro = await source.macro("DFF", "run-2")
    filings = await source.sec_submissions("0001045810", "run-2")
    news = await source.rss("run-2")
    await source.aclose()

    assert client.is_closed

    # Then
    assert universe[0].market_cap == Decimal(1000)
    assert universe[0].volume == 4200
    assert candles[0].close == Decimal(151)
    assert macro[0].value == Decimal("4.25")
    assert filings[0].form == "8-K"
    assert news[0].snippet == "Short snippet"
    assert all(
        item.provenance.captured_at == observed
        for item in (*universe, *candles, *macro, *filings, *news)
    )


@pytest.mark.anyio
async def test_ticker_news_builds_exact_google_search_query_and_parses_guid() -> None:
    # Given
    requested: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request)
        return httpx2.Response(
            200,
            request=request,
            text=(
                "<rss><channel><item><title>NVIDIA update</title>"
                "<description>NVDA demand</description>"
                "<link>https://news.example/story?token=redacted</link>"
                "<guid>story-1</guid>"
                "<pubDate>Fri, 10 Jul 2026 20:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When
    items = await source.ticker_news(
        TickerNewsQuery(ticker="NVDA", company_name="NVIDIA Corporation"), "ticker-run"
    )
    await source.aclose()

    # Then
    assert requested[0].url.host == "news.google.com"
    assert requested[0].url.path == "/rss/search"
    assert requested[0].url.params["q"] == '(NVDA OR "NVIDIA Corporation")'
    assert requested[0].url.params["hl"] == "en-US"
    assert requested[0].url.params["gl"] == "US"
    assert requested[0].url.params["ceid"] == "US:en"
    assert items[0].guid == "story-1"
    assert items[0].provenance.source == "google-news-rss"


@pytest.mark.anyio
async def test_ticker_news_rejects_malformed_xml_as_typed_validation_failure() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, text="<rss><broken>")

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(ValidationFailureError):
        _ = await source.ticker_news(
            TickerNewsQuery(ticker="NVDA", company_name="NVIDIA Corporation"), "invalid-news"
        )
    await source.aclose()


@pytest.mark.anyio
async def test_ticker_news_http_outage_remains_retryable() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, request=request)

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(HttpFailureError) as captured:
        _ = await source.ticker_news(
            TickerNewsQuery(ticker="NVDA", company_name="NVIDIA Corporation"), "news-outage"
        )
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is True
    assert decision.failure.error_code == "TRANSIENT_HTTP_FAILURE"


@pytest.mark.anyio
async def test_legacy_fred_csv_preserves_macro_output_contract() -> None:
    # Given
    observed = datetime(2026, 7, 14, 20, tzinfo=UTC)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            text="DATE,DFF\n2026-07-10,4.25\n",
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
        clock=lambda: observed,
    )

    # When
    macro = await source.macro("DFF", "legacy-fred-run")
    await source.aclose()

    # Then
    assert macro[0].series == "DFF"
    assert macro[0].observed_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert macro[0].value == Decimal("4.25")
    assert macro[0].provenance.execution_id == "legacy-fred-run"


@pytest.mark.anyio
async def test_current_fred_csv_uses_bounded_recent_dates() -> None:
    # Given
    observed = datetime(2026, 7, 14, 20, tzinfo=UTC)
    requested_urls: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(request.url)
        return httpx2.Response(
            200,
            request=request,
            text="observation_date,DFF\n2026-07-10,4.25\n",
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
        clock=lambda: observed,
    )

    # When
    macro = await source.macro("DFF", "current-fred-run")
    await source.aclose()

    # Then
    assert macro[0].value == Decimal("4.25")
    assert requested_urls[0].params["id"] == "DFF"
    assert requested_urls[0].params["cosd"] == "2026-06-14"
    assert requested_urls[0].params["coed"] == "2026-07-14"
    assert macro[0].provenance.source_ref == str(requested_urls[0])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        "observation_date,DFF\n",
        "observation_date,DFF\nnot-a-date,4.25\n",
        "observation_date,DFF\n2026-07-10,not-a-number\n",
        "unexpected,DFF\n2026-07-10,4.25\n",
    ],
    ids=("empty", "malformed-date", "malformed-value", "missing-date-header"),
)
async def test_unusable_fred_csv_is_a_typed_validation_failure(payload: str) -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, text=payload)

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(ValidationFailureError) as captured:
        _ = await source.macro("DFF", "invalid-fred-run")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is False
    assert decision.failure.error_code == "VALIDATION_FAILURE"


@pytest.mark.anyio
async def test_fred_transport_failure_remains_retryable() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = "stream reset"
        raise httpx2.ReadError(message, request=request)

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(httpx2.ReadError) as captured:
        _ = await source.macro("DFF", "transport-fred-run")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is True
    assert decision.failure.error_code == "TRANSPORT_FAILURE"


@pytest.mark.anyio
async def test_role04_uses_latest_dff_for_rate_and_risk() -> None:
    # Given
    observed = datetime(2026, 7, 14, 20, tzinfo=UTC)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            text="observation_date,DFF\n2026-07-10,9.99\n",
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
        clock=lambda: observed,
    )
    context = PipelineContext(request=PipelineRequest(ticker="NVDA", cycle_ts=observed))

    # When
    result = await MacroAnalysis(source).execute(context)
    await source.aclose()

    # Then
    assert result.macro_output is not None
    assert result.macro_output.rate == 9.99
    assert result.macro_output.risk_score == pytest.approx(0.8325)
    assert result.macro_output.regime is Regime.RISK_OFF
    assert "DFF 9.99%" in result.stages[-1].summary


@pytest.mark.anyio
async def test_valid_candle_payload_preserves_normalized_output_contract() -> None:
    # Given
    observed = datetime(2026, 7, 10, 20, tzinfo=UTC)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            json={
                "data": {
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "07/10/2026",
                                "open": "$149.00",
                                "high": "$152.00",
                                "low": "$148.00",
                                "close": "$151.00",
                                "volume": "100",
                            }
                        ]
                    }
                },
                "status": {"rCode": 200},
            },
        )

    client = build_http_client(transport=httpx2.MockTransport(handler))
    source = HttpMarketData(
        client,
        MarketDataEndpoints.defaults(),
        clock=lambda: observed,
    )

    # When
    candles = await source.candles("nvda", "baseline-run")
    await source.aclose()

    # Then
    assert len(candles) == 1
    assert candles[0].ticker == "NVDA"
    assert candles[0].opened_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert candles[0].open == Decimal("149.00")
    assert candles[0].high == Decimal("152.00")
    assert candles[0].low == Decimal("148.00")
    assert candles[0].close == Decimal("151.00")
    assert candles[0].volume == 100
    assert candles[0].provenance.source == "market-candles"
    assert candles[0].provenance.execution_id == "baseline-run"


@pytest.mark.anyio
async def test_nasdaq_candles_are_ascending_and_parse_grouped_currency_values() -> None:
    # Given
    observed = datetime(2026, 7, 14, 20, tzinfo=UTC)
    requested_urls: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(request.url)
        return httpx2.Response(
            200,
            request=request,
            json={
                "data": {
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "07/11/2026",
                                "close": "$152.00",
                                "volume": "1,234,567",
                                "open": "$151.00",
                                "high": "$153.00",
                                "low": "$150.00",
                            },
                            {
                                "date": "07/10/2026",
                                "close": "$151.00",
                                "volume": "987,654",
                                "open": "$149.00",
                                "high": "$152.00",
                                "low": "$148.00",
                            },
                        ]
                    }
                },
                "status": {"rCode": 200},
            },
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
        clock=lambda: observed,
    )

    # When
    candles = await source.candles("nvda", "nasdaq-run")
    await source.aclose()

    # Then
    assert [candle.opened_at.date().isoformat() for candle in candles] == [
        "2026-07-10",
        "2026-07-11",
    ]
    assert candles[0].open == Decimal("149.00")
    assert candles[0].volume == 987654
    assert candles[1].close == Decimal("152.00")
    assert requested_urls[0].host == "api.nasdaq.com"
    assert requested_urls[0].params["fromdate"] == "2025-06-09"
    assert requested_urls[0].params["todate"] == "2026-07-14"
    assert candles[0].provenance.source_ref == str(requested_urls[0])


@pytest.mark.anyio
async def test_nasdaq_embedded_failure_code_is_a_typed_fetch_error() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            request=request,
            json={"data": None, "status": {"rCode": 400}},
        )

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(ValidationFailureError) as captured:
        _ = await source.candles("NVDA", "embedded-failure-run")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is False
    assert decision.failure.error_code == "VALIDATION_FAILURE"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": None, "status": {"rCode": 200}},
        {"data": {"tradesTable": {"rows": []}}, "status": {"rCode": 200}},
        {
            "data": {
                "tradesTable": {
                    "rows": [
                        {
                            "date": "07/10/2026",
                            "close": "$151.00",
                            "volume": "100",
                            "open": "$149.00",
                            "low": "$148.00",
                        }
                    ]
                }
            },
            "status": {"rCode": 200},
        },
    ],
    ids=("null-data", "empty-rows", "malformed-row"),
)
async def test_nasdaq_unusable_candle_payload_is_a_typed_fetch_error(
    payload: dict[str, object],
) -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json=payload)

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(ValidationFailureError) as captured:
        _ = await source.candles("NVDA", "unusable-payload-run")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is False
    assert decision.failure.error_code == "VALIDATION_FAILURE"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [429, 503])
async def test_candle_http_outage_uses_retryable_http_classification(status_code: int) -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, request=request)

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(HttpFailureError) as captured:
        _ = await source.candles("NVDA", "http-outage-run")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is True
    assert decision.failure.error_code == "TRANSIENT_HTTP_FAILURE"


@pytest.mark.anyio
async def test_legacy_empty_values_payload_is_rejected_at_candle_boundary() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, request=request, json={"values": []})

    source = HttpMarketData(
        build_http_client(transport=httpx2.MockTransport(handler)),
        MarketDataEndpoints.defaults(),
    )

    # When / Then
    with pytest.raises(ValidationFailureError):
        _ = await source.candles("NVDA", "legacy-empty-run")
    await source.aclose()


@pytest.mark.anyio
async def test_http_failure_is_typed_with_source_context() -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, request=request)

    client = build_http_client(transport=httpx2.MockTransport(handler))
    endpoints = MarketDataEndpoints.defaults()

    # When / Then
    source = HttpMarketData(client, endpoints)
    with pytest.raises(HttpFailureError) as captured:
        _ = await source.screener("run-3")
    await source.aclose()
    decision = classify_failure(captured.value)
    assert decision.retryable is True
    assert decision.failure.error_code == "TRANSIENT_HTTP_FAILURE"


@pytest.mark.anyio
async def test_malformed_wire_payload_is_rejected_at_boundary() -> None:
    # Given
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"data": {"rows": [{"name": "missing ticker"}]}})

    client = build_http_client(transport=httpx2.MockTransport(handler))

    # When / Then
    source = HttpMarketData(client, MarketDataEndpoints.defaults())
    with pytest.raises(ValidationError):
        _ = await source.screener("malformed-run")
    await source.aclose()

    assert client.is_closed
