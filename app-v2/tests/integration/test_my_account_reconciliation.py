"""W2-1 완료 기준: 내 계좌 화면의 숫자가 원장의 숫자와 어긋나지 않는다.

§1-1은 화면이 원장을 **안 보는** 방식으로 틀렸다. 그때 이 대조가 있었다면
"체결 0건"이라는 화면과 "체결 46건"이라는 원장이 같은 테스트에서 만났다.
그래서 유저 화면의 완료 기준에 이 대조를 넣었다.

여기서 만나는 두 답은 서로 독립이다. ``equity``는 ``revalue_accounts``가
원장에 써 둔 값이고(D8), 보유 평가액은 화면이 ``account_holdings``로 따로
읽어 곱한 값이다. 둘이 같아야 화면이 자기만의 산수를 하지 않는다는 뜻이다.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantinue.api.my_account import my_account_view
from quantinue.db.domain_records import DailyBarWrite
from quantinue.db.postgres import PostgresRunStore
from quantinue.db.users import UserAccount

from .test_account_valuation import _seed_holding

DATABASE_URL = os.getenv("QUANTINUE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="disposable PostgreSQL URL not provided"
)

_DAY = date(2026, 7, 8)


@pytest.mark.anyio
async def test_the_page_total_equals_cash_plus_what_it_lists() -> None:
    """화면의 총자산과, 화면이 나열한 보유의 합이 어긋나면 둘 중 하나가 거짓말이다."""
    # Given: 10주를 100에 샀고 마지막 종가는 120이다
    assert DATABASE_URL is not None
    account_id, ticker = await _seed_holding(DATABASE_URL, "recon")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()
    await store.domain.save_daily_bars(
        (
            DailyBarWrite(
                trade_date=_DAY,
                ticker=ticker,
                open=Decimal("100.00"),
                high=Decimal("125.00"),
                low=Decimal("99.00"),
                close=Decimal("120.00"),
                volume=1000,
                source="test",
            ),
        )
    )
    _ = await store.domain.revalue_accounts(_DAY)

    # When: 화면이 읽는 그대로 읽어 화면 모델을 만든다
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text("SELECT broker_account_id, cash, equity FROM tb_account WHERE id = :aid"),
                {"aid": account_id},
            )
        ).one()
    await engine.dispose()
    account = UserAccount(
        account_id=account_id,
        broker_account_id=row.broker_account_id,
        inv_type="aggressive",
        status="active",
        cash=Decimal(str(row.cash)),
        equity=Decimal(str(row.equity)),
    )
    view = my_account_view(
        account, await store.domain.account_holdings(account_id), ()
    )

    # Then
    listed = sum(
        (holding.market_value or Decimal(0) for holding in view.holdings), start=Decimal(0)
    )
    assert view.holdings[0].ticker == ticker
    assert view.cash + listed == view.equity
    await store.close()


@pytest.mark.anyio
async def test_a_holding_the_ledger_cannot_price_is_shown_unpriced() -> None:
    """봉이 없는 보유에 값을 지어내면 화면이 원장보다 많이 안다고 말하게 된다."""
    # Given: 봉을 적재하지 않는다
    assert DATABASE_URL is not None
    account_id, _ = await _seed_holding(DATABASE_URL, "unpriced")
    store = PostgresRunStore(DATABASE_URL)
    await store.initialize()

    # When
    holdings = await store.domain.account_holdings(account_id)

    # Then
    assert holdings[0].mark_price is None
    assert holdings[0].mark_as_of is None
    await store.close()
