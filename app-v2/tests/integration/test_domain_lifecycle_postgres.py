"""Live PostgreSQL proof for canonical pipeline domain writes."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.api.schemas import TerminalRunDetailView
from quantinue.broker.mock import MockBroker
from quantinue.core.config import DatabaseMode, Settings
from quantinue.core.contracts import PipelineRequest
from quantinue.db.postgres import PostgresRunStore
from quantinue.db.reviews import PostgresReviewRepository
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.main import create_app
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator

_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")
_INT = TypeAdapter(int)


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_rejects_orphan_technical_snapshot() -> None:
    # Given
    assert _URL is not None
    engine = create_async_engine(_URL)

    # When / Then
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_technical(
                    trade_date,ticker,close,rs_20,vol_ratio,ret_5d,ret_20d,atr_pct,
                    high_252_ratio,rsi,macd,ma20,ma50,trend)
                    VALUES ('2031-01-02','ORPHAN',100,1,1,1,1,1,1,50,1,99,98,'up')"""
                )
            )
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_postgres_rejects_phase_two_sell_strategist_signal() -> None:
    # Given: the parent universe and daily-pick rows required by a strategist signal
    assert _URL is not None
    trade_date = datetime(2031, 1, 3, tzinfo=UTC).date()
    ticker = "SELLX"
    cycle_ts = datetime(2031, 1, 3, 13, 0, tzinfo=UTC)
    engine = create_async_engine(_URL)
    try:
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                    VALUES (:trade_date,:ticker,'Sell Contract',1)"""
                ),
                {"trade_date": trade_date, "ticker": ticker},
            )
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score
                    ) VALUES (:trade_date,:ticker,:trade_date,'backfill',1,'test',1)"""
                ),
                {"trade_date": trade_date, "ticker": ticker},
            )

        # When / Then: PostgreSQL receives a phase-two strategist side
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                _ = await connection.execute(
                    text(
                        """INSERT INTO tb_strategist_signals(
                        trade_date,ticker,cycle_ts,inv_type,side,conviction,signal_consensus,
                        summary,evidence,sizing_hint,decision_close,current_price,day_high,
                        day_low,close_prev,volume,turnover,high_52w,low_52w
                        ) VALUES (:trade_date,:ticker,:cycle_ts,'aggressive','sell',0.8,2,
                        'phase-two','{}','{}',100,100,101,99,99,1,100,120,80)"""
                    ),
                    {
                        "trade_date": trade_date,
                        "ticker": ticker,
                        "cycle_ts": cycle_ts,
                    },
                )
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.skipif(_URL is None, reason="disposable PostgreSQL URL not provided")
async def test_pipeline_persists_real_domain_ids_and_reconciles_reserved_order() -> None:
    # Given
    assert _URL is not None
    store = PostgresRunStore(_URL)
    await store.initialize()
    orchestrator = PipelineOrchestrator(
        build_roles(DeterministicAnalyzer(), MockBroker(), store=store), store
    )

    # When
    run = await orchestrator.run(
        PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 2, 13, 0, tzinfo=UTC))
    )
    rerun = await orchestrator.run(
        PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 2, 13, 0, tzinfo=UTC))
    )

    # Then
    engine = create_async_engine(_URL)
    async with engine.connect() as connection:
        signal_count = _INT.validate_python(
            await connection.scalar(
                text("""SELECT count(*) FROM tb_strategist_signals
                WHERE ticker='NVDA' AND cycle_ts='2026-07-02 13:00:00+00'""")
            )
        )
        verdict_count = _INT.validate_python(
            await connection.scalar(
                text("""SELECT count(*) FROM tb_critic_verdict v
                JOIN tb_strategist_signals s ON s.id=v.signal_id
                WHERE s.ticker='NVDA' AND s.cycle_ts='2026-07-02 13:00:00+00'""")
            )
        )
        order_count = _INT.validate_python(
            await connection.scalar(
                text("""SELECT count(*) FROM tb_order o
                JOIN tb_strategist_signals s ON s.id=o.signal_id
                WHERE s.ticker='NVDA' AND s.cycle_ts='2026-07-02 13:00:00+00'""")
            )
        )
        fill_count = _INT.validate_python(
            await connection.scalar(
                text("""SELECT count(*) FROM tb_fill f JOIN tb_order o ON o.id=f.order_id
                JOIN tb_strategist_signals s ON s.id=o.signal_id
                WHERE s.ticker='NVDA' AND s.cycle_ts='2026-07-02 13:00:00+00'""")
            )
        )
        source_count = _INT.validate_python(
            await connection.scalar(
                text(
                    """SELECT (SELECT count(*) FROM tb_disclosure
                       WHERE filing_no='fixture-filing')
                    + (SELECT count(*) FROM tb_news
                       WHERE news_key='https://example.invalid/fixture-news')
                    + (SELECT count(*) FROM tb_disclosure_signal WHERE ticker='NVDA'
                       AND cycle_ts='2026-07-02 13:00:00+00')
                    + (SELECT count(*) FROM tb_news_signal WHERE ticker='NVDA'
                       AND cycle_ts='2026-07-02 13:00:00+00')"""
                )
            )
        )
        signal_id = _INT.validate_python(
            await connection.scalar(
                text("""SELECT id FROM tb_strategist_signals WHERE ticker='NVDA'
                AND cycle_ts='2026-07-02 13:00:00+00'""")
            )
        )
        universe = (
            await connection.execute(
                text("""SELECT company_name, market_cap FROM tb_universe
                WHERE as_of_date='2026-07-02' AND ticker='NVDA'""")
            )
        ).one()
        technical = (
            await connection.execute(
                text("""SELECT close, trend FROM tb_technical
                WHERE trade_date='2026-07-02' AND ticker='NVDA'""")
            )
        ).one()
        daily_pick = (
            await connection.execute(
                text("""SELECT bucket, rank, score FROM tb_daily_pick
                WHERE trade_date='2026-07-02' AND ticker='NVDA'""")
            )
        ).one()
        macro = (
            await connection.execute(
                text("""SELECT regime, risk_score FROM tb_macro
                WHERE as_of='2026-07-02 13:00:00+00'""")
            )
        ).one()
    async with engine.begin() as connection:
        _ = await connection.execute(
            text("UPDATE tb_strategist_signals SET decision_close=90 WHERE id=:id"),
            {"id": signal_id},
        )
    reviews = PostgresReviewRepository(_URL)
    await reviews.initialize()
    review_signal = await reviews.get_signal(signal_id)
    await reviews.close()
    await engine.dispose()
    settings = Settings.model_validate(
        {"database_mode": DatabaseMode.POSTGRES, "database_url": _URL}
    )
    app = create_app(settings, store=store)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        processed = await client.post(f"/api/reviews/{signal_id}/process")
        detail = await client.get(f"/api/runs/{run.run_id}")
        terminal_detail = await client.get(f"/api/runs/{run.run_id}/detail")
    assert run.order is not None
    assert rerun.run_id == run.run_id
    assert (signal_count, verdict_count, order_count, fill_count) == (1, 1, 1, 1)
    assert source_count == 4
    assert tuple(universe) == ("NVIDIA Corporation", 3_210_000_000_000)
    assert tuple(technical) == (128.40, "up")
    assert tuple(daily_pick) == ("trend_leader", 1, 0.82)
    assert tuple(macro) == ("neutral", Decimal("0.420"))
    assert review_signal is not None
    assert review_signal.base_price != 90
    assert processed.json()["status"] == "completed"
    assert detail.json()["review"]["outcome"] in {"hit", "miss"}
    assert tuple(
        role.component
        for role in TerminalRunDetailView.model_validate_json(terminal_detail.content).roles
    ) == tuple(f"{component:02d}" for component in range(1, 12))
