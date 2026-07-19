"""Atomic PostgreSQL accounting for one app-owned simulated buy."""

from decimal import Decimal

from pydantic import TypeAdapter
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from quantinue.core.ontology import FillSide
from quantinue.db.domain_records import (
    AccountWrite,
    CompletedBuyWrite,
    InsufficientSimulatedCashError,
)

_INT = TypeAdapter(int)


async def initialize_account(engine: AsyncEngine, table: Table, value: AccountWrite) -> int:
    """Insert one local account without changing an existing durable balance."""
    fields = {
        "currency": value.currency,
        "cash": value.cash,
        "equity": value.equity,
        "buying_power": value.buying_power,
        "is_paper": True,
    }
    if value.inv_type is not None:
        fields["inv_type"] = value.inv_type
    async with engine.begin() as connection:
        _ = await connection.execute(
            insert(table)
            .values(broker_account_id=value.broker_account_id, **fields)
            .on_conflict_do_nothing(index_elements=["broker_account_id"])
        )
        return _INT.validate_python(
            await connection.scalar(
                select(table.c.id).where(table.c.broker_account_id == value.broker_account_id)
            )
        )


async def record_completed_buy(
    engine: AsyncEngine,
    order_table: Table,
    fill_table: Table,
    account_table: Table,
    value: CompletedBuyWrite,
) -> int:
    """Insert one unique fill and debit cash in the same transaction."""
    async with engine.begin() as connection:
        existing_fill_id = await connection.scalar(
            select(fill_table.c.id).where(fill_table.c.broker_fill_id == value.broker_fill_id)
        )
        if existing_fill_id is not None:
            return _INT.validate_python(existing_fill_id)
        order = (
            (
                await connection.execute(
                    select(order_table.c.id, order_table.c.account_id)
                    .where(order_table.c.idempotency_key == value.idempotency_key)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        order_id = _INT.validate_python(order["id"])
        account_id = _INT.validate_python(order["account_id"])
        existing_fill_id = await connection.scalar(
            select(fill_table.c.id).where(fill_table.c.broker_fill_id == value.broker_fill_id)
        )
        if existing_fill_id is not None:
            return _INT.validate_python(existing_fill_id)
        notional = Decimal(value.quantity) * value.price
        if value.side is FillSide.SELL:
            # 매도에는 잔고 검사가 없다 — 현금을 쓰는 게 아니라 받는 쪽이라
            # 막을 이유가 없다. 보유 수량이 충분한지는 이 함수가 아니라 청산을
            # 계획하는 단계가 검사한다: 여기는 "브로커가 이미 체결했다"는
            # 사실을 원장에 반영할 뿐이라 되돌릴 권한이 없다. 여기서 거절하면
            # 브로커에는 체결이 있는데 원장에는 없는 상태가 되어 더 나쁘다.
            _ = await connection.execute(
                account_table.update()
                .where(account_table.c.id == account_id)
                .values(
                    cash=account_table.c.cash + notional,
                    buying_power=account_table.c.buying_power + notional,
                )
            )
        else:
            debited = await connection.scalar(
                account_table.update()
                .where(
                    account_table.c.id == account_id,
                    account_table.c.cash >= notional,
                    account_table.c.buying_power >= notional,
                )
                .values(
                    cash=account_table.c.cash - notional,
                    buying_power=account_table.c.buying_power - notional,
                )
                .returning(account_table.c.id)
            )
            if debited is None:
                available = await connection.scalar(
                    select(account_table.c.cash).where(account_table.c.id == account_id)
                )
                raise InsufficientSimulatedCashError(
                    available=Decimal(str(available)), required=notional
                )
        _ = await connection.execute(
            order_table.update()
            .where(order_table.c.id == order_id)
            .values(status="filled", broker_order_id=value.broker_order_id)
        )
        return _INT.validate_python(
            await connection.scalar(
                insert(fill_table)
                .values(
                    order_id=order_id,
                    side=value.side.value,
                    quantity=value.quantity,
                    price=value.price,
                    filled_at=value.filled_at,
                    broker_fill_id=value.broker_fill_id,
                )
                .returning(fill_table.c.id)
            )
        )
