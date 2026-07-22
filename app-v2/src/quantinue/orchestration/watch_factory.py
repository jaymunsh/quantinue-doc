"""Assemble the intraday polling and streaming runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, assert_never, runtime_checkable

from quantinue.broker.mock import MockBroker
from quantinue.core.config import DataMode
from quantinue.market_data.alpaca_quotes import AlpacaQuoteSource
from quantinue.market_data.alpaca_stream import AlpacaTradeStream
from quantinue.market_data.fixture import FixtureMarketData
from quantinue.notify.telegram import build_failure_notifier
from quantinue.orchestration.intraday_rejudge import IntradayRejudgeEngine, IntradaySellDomain
from quantinue.orchestration.watch_runner import LatestTradeSource, WatchDomain, WatchRunner
from quantinue.roles.allocation.job import AllocationJob
from quantinue.roles.analysis.job import AnalysisJob
from quantinue.roles.exits.job import ExitJob

if TYPE_CHECKING:
    from quantinue.core.config import Settings
    from quantinue.llm.provider import LlmAnalyzer
    from quantinue.orchestration.policy import Mvp2Config


class _WatchDomain(WatchDomain, IntradaySellDomain, Protocol):
    pass


@runtime_checkable
class _WatchStore(Protocol):
    @property
    def domain(self) -> _WatchDomain: ...


def build_watch_runner(
    settings: Settings,
    config: Mvp2Config,
    *,
    store: object,
    quotes: LatestTradeSource | None = None,
    analyzer: LlmAnalyzer | None = None,
) -> WatchRunner | None:
    """Bind shared exit, analysis, allocation, polling, and live-stream paths."""
    if not isinstance(store, _WatchStore):
        return None
    domain = store.domain
    source = quotes
    stream = None
    if source is None:
        match settings.data_mode:
            case DataMode.FIXTURE:
                source = FixtureMarketData()
            case DataMode.PUBLIC:
                key = settings.alpaca_api_key.get_secret_value().strip()
                secret = settings.alpaca_secret_key.get_secret_value().strip()
                if key and secret:
                    source = AlpacaQuoteSource(
                        key_id=key,
                        secret_key=secret,
                        symbols_per_request=config.market_data.symbols_per_request,
                    )
                    if config.watch.stream.enabled:
                        stream = AlpacaTradeStream(
                            key_id=key,
                            secret_key=secret,
                            config=config.watch.stream,
                        )
            case unreachable:
                assert_never(unreachable)
    if source is None:
        return None
    exit_job = ExitJob(
        store=store,
        broker=MockBroker(),
        time_exit_bdays=config.exits.time_exit_bdays,
    )
    rejudge = None
    if config.watch.rejudge.enabled and analyzer is not None:
        rejudge = IntradayRejudgeEngine(
            domain=domain,
            jobs=tuple(
                AnalysisJob(
                    store=store,
                    analyzer=analyzer,
                    gates=config.gates,
                    profile=profile,
                    profile_name=name,
                    headlines_per_ticker=config.news.headlines_per_ticker,
                )
                for name, profile in config.profiles.items()
            ),
            exits=exit_job,
            allocation=AllocationJob(
                store=store,
                broker=MockBroker(),
                profiles=config.profiles,
                gates=config.gates,
                allocation=config.allocation,
            ),
        )
    return WatchRunner(
        config.watch,
        domain=domain,
        quotes=source,
        exits=exit_job,
        notifier=build_failure_notifier(settings),
        rejudge=rejudge,
        stream=stream,
    )
