"""Select the market data adapter this installation should collect through.

구 11단계 러너의 조립기(``orchestration/factory.py``)에 얹혀 있던 함수들이다.
잡 경로도 같은 어댑터를 쓰는데, 러너와 한 파일에 있으면 러너를 지울 때 수집이
함께 죽는다. 소유자를 어댑터 쪽으로 옮겼다 — 선택 규칙은 데이터 소스의 관심사이지
파이프라인 조립의 관심사가 아니다.
"""

from typing import assert_never

import httpx2

from quantinue.core.config import DataMode, Settings
from quantinue.market_data import (
    FixtureMarketData,
    HttpMarketData,
    MarketDataEndpoints,
    build_http_client,
    fetch_fred_csv,
)


def build_market_data(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> FixtureMarketData | HttpMarketData:
    """Select deterministic fixtures or no-key public HTTP adapters."""
    match settings.data_mode:
        case DataMode.FIXTURE:
            return FixtureMarketData()
        case DataMode.PUBLIC:
            return build_public_market_data(transport)
        case unreachable:
            assert_never(unreachable)


def build_public_market_data(
    transport: httpx2.AsyncBaseTransport | None = None,
) -> HttpMarketData:
    """Build the application-owned no-key HTTP adapter."""
    return HttpMarketData(
        build_http_client(transport=transport),
        MarketDataEndpoints.defaults(),
        fred_fetcher=None if transport is not None else fetch_fred_csv,
    )
