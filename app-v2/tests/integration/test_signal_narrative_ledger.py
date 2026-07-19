"""Remaining-work B: the judgement narrative and lineage land in the ledger.

`bull_case`·`key_risk`·`src_macro_at`·model lineage columns existed since M2
but the new analysis job never wrote them — the prompt was producing the
narrative and the ledger was dropping it.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.db.domain_records import StrategistSignalWrite
from quantinue.db.postgres import PostgresRunStore

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 20)
_MIDNIGHT = datetime.combine(_DAY, time(), tzinfo=UTC)


async def _seed_scope(ticker: str, *, macro_at: datetime | None = None) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        _ = await connection.execute(
            text(
                """INSERT INTO tb_universe(as_of_date,ticker,company_name,market_cap)
                VALUES (:day,:ticker,'Narrative',1) ON CONFLICT DO NOTHING"""
            ),
            {"day": _DAY, "ticker": ticker},
        )
        _ = await connection.execute(
            text(
                """INSERT INTO tb_daily_pick(
                    trade_date,ticker,universe_as_of,bucket,rank,sector,score)
                VALUES (:day,:ticker,:day,'backfill',1,'test',1)
                ON CONFLICT DO NOTHING"""
            ),
            {"day": _DAY, "ticker": ticker},
        )
        if macro_at is not None:
            _ = await connection.execute(
                text(
                    """INSERT INTO tb_macro(as_of,regime,vix,nasdaq_ret,sp500_ret,
                        rate,dollar,risk_score)
                    VALUES (:at,'neutral',20,0,0,3.6,100,0.3)
                    ON CONFLICT DO NOTHING"""
                ),
                {"at": macro_at},
            )
    await engine.dispose()


def _write(ticker: str, **overrides: object) -> StrategistSignalWrite:
    base: dict[str, object] = {
        "run_id": "narrative-run",
        "trade_date": _DAY,
        "ticker": ticker,
        "cycle_ts": _MIDNIGHT,
        "side": "buy",
        "conviction": Decimal("0.800"),
        "summary": "narrative fixture",
        "decision_close": Decimal(50),
        "evidence": ("narrative-run:e1",),
        "inv_type": "aggressive",
    }
    base.update(overrides)
    return StrategistSignalWrite(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_the_narrative_and_lineage_columns_are_written() -> None:
    # Given
    assert DATABASE_URL is not None
    macro_at = _MIDNIGHT
    await _seed_scope("NARR", macro_at=macro_at)
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    signal_id = await store.domain.save_signal(
        _write(
            "NARR",
            bull_case="거래량이 돌파를 확인",
            key_risk="국면 반전",
            src_macro_at=macro_at,
            model_provider="local",
            model_name="qwen-test",
            prompt_version="2026-07-20.1",
            input_hash="a" * 64,
        )
    )

    # Then
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """SELECT bull_case,key_risk,src_macro_at,model_provider,
                        model_name,prompt_version,input_hash
                    FROM tb_strategist_signals WHERE id=:id"""
                ),
                {"id": signal_id},
            )
        ).one()
    await engine.dispose()
    assert row.bull_case == "거래량이 돌파를 확인"
    assert row.key_risk == "국면 반전"
    assert row.src_macro_at == macro_at
    assert row.model_provider == "local"
    assert row.model_name == "qwen-test"
    assert row.prompt_version == "2026-07-20.1"
    assert row.input_hash == "a" * 64
    await store.close()


@pytest.mark.anyio
async def test_an_absent_narrative_stays_null_not_empty_string() -> None:
    """NULL과 ""는 다르다 — NULL은 "안 만들었다", ""는 "만들었는데 비었다"다."""
    # Given
    assert DATABASE_URL is not None
    await _seed_scope("NONARR")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    signal_id = await store.domain.save_signal(_write("NONARR"))

    # Then
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT bull_case,key_risk,src_macro_at FROM tb_strategist_signals "
                    "WHERE id=:id"
                ),
                {"id": signal_id},
            )
        ).one()
    await engine.dispose()
    assert row.bull_case is None
    assert row.key_risk is None
    assert row.src_macro_at is None
    await store.close()
