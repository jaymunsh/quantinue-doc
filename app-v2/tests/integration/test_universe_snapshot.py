"""Phase 2: the weekly universe snapshot and the readers that find the latest one."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest

from quantinue.db.postgres import PostgresRunStore
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerOutput,
)

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)


def _snapshot(as_of: date, *tickers: str) -> UniverseScreenerOutput:
    return UniverseScreenerOutput(
        run_id=f"universe:{as_of.isoformat()}",
        generated_at=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
        members=tuple(
            UniverseMember(
                as_of_date=as_of,
                ticker=ticker,
                company_name=f"{ticker} Inc",
                market_cap=1_000_000 + index,
                evidence_ids=(f"universe:{as_of.isoformat()}:{ticker}",),
            )
            for index, ticker in enumerate(tickers)
        ),
    )


@pytest.mark.anyio
async def test_universe_tickers_are_ordered_by_market_cap() -> None:
    """랭킹 순서가 저장을 왕복해도 살아남아야 상위 N이 의미를 갖는다."""
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    as_of = date(2026, 6, 15)
    await store.domain.save_universe(_snapshot(as_of, "UNVSML", "UNVMID", "UNVBIG"))

    # When
    tickers = await store.domain.universe_tickers(as_of)

    # Then: _snapshot이 뒤로 갈수록 시총을 키우므로 역순이 나와야 한다
    assert tickers == ("UNVBIG", "UNVMID", "UNVSML")
    await store.close()


@pytest.mark.anyio
async def test_an_empty_universe_has_no_latest_snapshot() -> None:
    # Given
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    tickers = await store.domain.universe_tickers(date(1999, 1, 4))

    # Then
    assert tickers == ()
    await store.close()


@pytest.mark.anyio
async def test_the_job_ledger_is_what_points_at_the_usable_snapshot() -> None:
    """구 러너가 같은 테이블에 1행짜리를 써도 주간 스냅샷이 가려지면 안 된다.

    D6 점진 교체 중이라 구 11단계 러너의 role_01과 새 유니버스 잡이 같은
    ``tb_universe``에 쓴다. fixture 모드의 role_01은 오늘 날짜로 1행만 쓰므로
    최대 as_of_date를 믿으면 그게 이긴다 — 그래서 소비자는 유니버스 **잡**이
    성공한 날짜를 원장에서 읽는다.
    """
    # Given: 잡이 만든 주간 스냅샷(3종목)
    assert DATABASE_URL is not None
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    weekly = date(2026, 5, 11)
    await store.domain.save_universe(_snapshot(weekly, "LEDA", "LEDB", "LEDC"))
    _ = await store.domain.reserve_job_run("universe", weekly)
    await store.domain.finish_job_run("universe", weekly, succeeded=True)

    # And: 구 러너가 더 최근 날짜로 1행을 남긴다
    await store.domain.save_universe(_snapshot(date(2026, 5, 13), "LEDFIX"))

    # When
    snapshot = await store.domain.last_job_success("universe")

    # Then: 원장은 여전히 주간 스냅샷을 가리킨다
    assert snapshot == weekly
    assert await store.domain.universe_tickers(snapshot) == ("LEDC", "LEDB", "LEDA")
    await store.close()
