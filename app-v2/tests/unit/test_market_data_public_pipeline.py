"""Public data composition runs end to end against a fake HTTP wire."""

from datetime import UTC, date, datetime, timedelta

import httpx2
import pytest

from quantinue.broker.mock import MockBroker
from quantinue.core.config import DataMode, Settings
from quantinue.core.contracts import PipelineRequest
from quantinue.db.store import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.market_data import HttpMarketData
from quantinue.orchestration.factory import build_market_data, build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator


def _public_response(request: httpx2.Request) -> httpx2.Response:
    host = request.url.host
    if host == "api.nasdaq.com":
        if request.url.path.endswith("/historical"):
            payload = {
                "data": {
                    "tradesTable": {
                        "rows": [
                            {
                                "date": (date(2026, 7, 10) - timedelta(days=day)).strftime(
                                    "%m/%d/%Y"
                                ),
                                "open": f"${149 - day / 10}",
                                "high": f"${152 - day / 10}",
                                "low": f"${148 - day / 10}",
                                "close": f"${151 - day / 10}",
                                "volume": str(100 + day),
                            }
                            for day in range(60)
                        ]
                    }
                },
                "status": {"rCode": 200},
            }
            return httpx2.Response(200, json=payload, request=request)
        payload = {
            "data": {
                "rows": [
                    {
                        "symbol": "NVDA" if rank == 0 else f"T{rank:03d}",
                        "name": "NVIDIA" if rank == 0 else f"Company {rank}",
                        "marketCap": str(1000 - rank),
                        "lastsale": "$150",
                        "volume": 42,
                    }
                    for rank in range(20)
                ]
            }
        }
        return httpx2.Response(200, json=payload, request=request)
    if host == "fred.stlouisfed.org":
        return httpx2.Response(200, text="DATE,DFF\n2026-07-10,4.25\n", request=request)
    if host == "data.sec.gov":
        recent = {
            "accessionNumber": ["0001"],
            "filingDate": ["2026-07-10"],
            "form": ["8-K"],
            "primaryDocument": ["x.htm"],
        }
        payload = {"cik": "1045810", "name": "NVIDIA CORP", "filings": {"recent": recent}}
        return httpx2.Response(200, json=payload, request=request)
    if host == "news.google.com":
        rss = (
            "<rss><channel><item><title>NVDA NVIDIA update</title>"
            "<description>NVIDIA demand expands</description>"
            "<link>https://news.example/nvda</link>"
            "<guid>nvda-news-1</guid>"
            "<pubDate>Fri, 10 Jul 2026 20:00:00 GMT</pubDate></item></channel></rss>"
        )
        return httpx2.Response(200, text=rss, request=request)
    rss = (
        "<rss><channel><item><title>NVIDIA update</title>"
        "<link>https://example.test/nvda</link><description>Short snippet</description>"
        "<pubDate>Fri, 10 Jul 2026 20:00:00 GMT</pubDate></item></channel></rss>"
    )
    return httpx2.Response(200, text=rss, request=request)


@pytest.mark.anyio
async def test_public_mode_runs_pipeline_through_fake_http_wire() -> None:
    # Given
    settings = Settings(data_mode=DataMode.PUBLIC)
    source = build_market_data(settings, httpx2.MockTransport(_public_response))
    assert isinstance(source, HttpMarketData)
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), source)
    orchestrator = PipelineOrchestrator(roles, InMemoryRunStore())

    # When
    result = await orchestrator.run(
        PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 10, 20, tzinfo=UTC))
    )
    await source.aclose()

    # Then
    data_traces = tuple(
        item for item in result.evidence_trace if item.component in {"01", "02", "04", "05", "06"}
    )
    assert len(result.stages) == 11
    assert tuple(item.source for item in data_traces) == (
        "nasdaq-screener",
        "market-candles",
        "macro-feed",
        "sec-submissions",
        "google-news-rss",
    )
    assert all(item.source_ref.startswith("https://") for item in data_traces)
    assert all(item.observed_at <= item.captured_at == result.cycle_ts for item in data_traces)
    assert all(item.confidence == 0.9 for item in data_traces)
    assert all(item.run_id == result.run_id for item in data_traces)
    role_facts = {role.component: dict(role.facts) for role in result.detail.roles}
    assert role_facts["05"]["모델 판정"]
    assert role_facts["05"]["분석 이유"]
    assert role_facts["05"]["모델"] == "deterministic-mock-v1"
    assert role_facts["06"]["모델 판정"]
    assert role_facts["06"]["분석 이유"]
    assert role_facts["06"]["모델"] == "deterministic-mock-v1"
