"""Dependency composition for the default 01 to 11 pipeline."""

from pathlib import Path
from typing import Final, assert_never

import httpx2

from quantinue.broker.provider import AlpacaBroker, Broker, MockBroker, build_broker
from quantinue.core.config import BrokerMode, DatabaseMode, DataMode, Settings
from quantinue.db.postgres import PostgresRunStore
from quantinue.db.store import InMemoryRunStore, RunStore
from quantinue.llm.provider import DeterministicAnalyzer, LlmAnalyzer, build_llm_analyzer
from quantinue.market_data import (
    FixtureMarketData,
    HttpMarketData,
    MarketData,
    MarketDataEndpoints,
    build_http_client,
    fetch_fred_csv,
)
from quantinue.orchestration.pipeline import PipelineOrchestrator, PipelineRole
from quantinue.orchestration.policy import (
    DEFAULT_PIPELINE_POLICY,
    PipelinePolicy,
    ScreeningConfig,
    load_mvp2_config,
    load_pipeline_policy,
)
from quantinue.roles.role_01_universe_screener.service import UniverseScreener
from quantinue.roles.role_02_technical_analysis.service import TechnicalAnalysis
from quantinue.roles.role_03_daily_screener.service import DailyScreener
from quantinue.roles.role_04_macro_analysis.service import MacroAnalysis
from quantinue.roles.role_05_disclosure_analysis.service import DisclosureAnalysis
from quantinue.roles.role_06_news_analysis.service import NewsAnalysis
from quantinue.roles.role_07_strategist.service import Strategist
from quantinue.roles.role_08_critic.service import Critic
from quantinue.roles.role_09_risk_portfolio.service import RiskPortfolio
from quantinue.roles.role_10_order_execution.service import OrderExecution
from quantinue.roles.role_11_reviewer.service import Reviewer

DEFAULT_SCREENING: Final[ScreeningConfig] = ScreeningConfig()


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


def build_roles(  # noqa: PLR0913 - one composition seam per replaceable collaborator
    analyzer: LlmAnalyzer,
    broker: Broker,
    market_data: MarketData | None = None,
    store: RunStore | None = None,
    policy: PipelinePolicy = DEFAULT_PIPELINE_POLICY,
    screening: ScreeningConfig = DEFAULT_SCREENING,
) -> tuple[PipelineRole, ...]:
    """Compose the replaceable role implementations in canonical order."""
    selected_store = store or InMemoryRunStore()
    return (
        UniverseScreener(market_data, screening),
        TechnicalAnalysis(market_data, screening),
        DailyScreener(screening),
        MacroAnalysis(market_data),
        DisclosureAnalysis(analyzer, market_data=market_data),
        NewsAnalysis(analyzer, market_data=market_data),
        Strategist(
            analyzer,
            policy.thresholds.minimum_confidence,
            policy.thresholds.strategist_buy_score,
        ),
        Critic(
            analyzer,
            policy.thresholds.minimum_confidence,
            policy.thresholds.critic_approval_score,
        ),
        RiskPortfolio(
            store=selected_store,
            daily_new_order_cap=policy.daily_new_order_cap,
            max_app_order_exposure_usd=policy.max_app_order_exposure_usd,
            maximum_risk_score=policy.thresholds.maximum_risk_score,
            stop_loss_ratio=policy.stop_loss_ratio,
            take_profit_ratio=policy.take_profit_ratio,
        ),
        OrderExecution(broker, selected_store),
        Reviewer(),
    )


def build_default_orchestrator(
    store: RunStore | None = None,
    analyzer: LlmAnalyzer | None = None,
    broker: Broker | None = None,
) -> PipelineOrchestrator:
    """Build an offline-safe pipeline for tests and direct use."""
    selected_store = store or InMemoryRunStore()
    selected_analyzer = analyzer or DeterministicAnalyzer()
    selected_broker = broker or MockBroker()
    return PipelineOrchestrator(
        build_roles(selected_analyzer, selected_broker, store=selected_store), selected_store
    )


def build_configured_orchestrator(
    settings: Settings,
    *,
    market_transport: httpx2.AsyncBaseTransport | None = None,
) -> tuple[PipelineOrchestrator, RunStore]:
    """Build adapters from validated environment settings."""
    match settings.database_mode:
        case DatabaseMode.MEMORY:
            store: RunStore = InMemoryRunStore(settings.simulated_account_opening_cash_usd)
            broker = build_broker(settings)
        case DatabaseMode.POSTGRES:
            postgres_store = PostgresRunStore(
                str(settings.database_url), settings.simulated_account_opening_cash_usd
            )
            store = postgres_store
            match settings.broker_mode:
                case BrokerMode.MOCK:
                    broker = build_broker(settings)
                case BrokerMode.ALPACA:
                    broker = AlpacaBroker(
                        settings,
                        reservations=postgres_store.order_reservations,
                    )
                case unreachable_broker:
                    assert_never(unreachable_broker)
        case unreachable_database:
            assert_never(unreachable_database)
    match settings.data_mode:
        case DataMode.FIXTURE:
            market_data: MarketData | None = None
            resources: tuple[HttpMarketData, ...] = ()
        case DataMode.PUBLIC:
            public_market_data = build_public_market_data(market_transport)
            market_data = public_market_data
            resources = (public_market_data,)
        case unreachable_data_mode:
            assert_never(unreachable_data_mode)
    config_path = Path(__file__).parents[3] / "config" / "pipeline.yaml"
    screening = load_mvp2_config(config_path).screening
    policy = load_pipeline_policy(config_path).model_copy(
        update={
            "daily_new_order_cap": settings.daily_new_order_cap,
            "max_app_order_exposure_usd": settings.max_app_order_exposure_usd,
        }
    )
    resolved_settings = policy.apply_model_defaults(settings)
    orchestrator = PipelineOrchestrator(
        build_roles(
            build_llm_analyzer(resolved_settings),
            broker,
            market_data,
            store,
            policy,
            screening,
        ),
        store,
        policy=policy,
    )
    for resource in resources:
        orchestrator.own_resource(resource)
    return orchestrator, store
