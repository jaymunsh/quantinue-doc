"""Only active accounts subscribe to a cycle.

A paused or closed account must not receive orders, and the research stages
(01–08) must not multiply with the account count — they run once per cycle.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.domain_records import AccountWrite

DATABASE_URL = os.environ.get("QUANTINUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="requires PostgreSQL")


async def _domain() -> PostgresDomainRepository:
    assert DATABASE_URL is not None
    domain = PostgresDomainRepository(DATABASE_URL)
    await domain.initialize()
    return domain


async def _account(broker_account_id: str, inv_type: str, status: str = "active") -> int:
    domain = await _domain()
    account_id = await domain.save_account(
        AccountWrite(
            broker_account_id=broker_account_id,
            cash=Decimal("100000.00"),
            equity=Decimal("100000.00"),
            buying_power=Decimal("100000.00"),
            inv_type=inv_type,
        )
    )
    if status != "active":
        async with domain.engine.begin() as connection:
            _ = await connection.execute(
                text("UPDATE tb_account SET status = :status WHERE id = :id"),
                {"status": status, "id": account_id},
            )
    return account_id


@pytest.mark.anyio
async def test_active_accounts_are_returned_with_their_profile_type() -> None:
    account_id = await _account("SUB-ACTIVE-01", "conservative")
    domain = await _domain()

    accounts = await domain.active_accounts()

    found = next(item for item in accounts if item.account_id == account_id)
    assert found.inv_type == "conservative"
    assert found.equity == Decimal("100000.00")


@pytest.mark.anyio
async def test_a_paused_account_does_not_subscribe() -> None:
    paused = await _account("SUB-PAUSED-01", "aggressive", status="paused")
    domain = await _domain()

    assert all(item.account_id != paused for item in await domain.active_accounts())


@pytest.mark.anyio
async def test_a_closed_account_does_not_subscribe() -> None:
    closed = await _account("SUB-CLOSED-01", "aggressive", status="closed")
    domain = await _domain()

    assert all(item.account_id != closed for item in await domain.active_accounts())


@pytest.mark.anyio
async def test_accounts_come_back_in_a_stable_order() -> None:
    # 계좌 순서가 흔들리면 같은 사이클이 실행마다 다른 계좌부터 한도를 소진한다.
    domain = await _domain()

    first = [item.account_id for item in await domain.active_accounts()]
    second = [item.account_id for item in await domain.active_accounts()]

    assert first == second
    assert first == sorted(first)
